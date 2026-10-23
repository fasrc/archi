"""Service templates must not hard-code a compression format for a moving download.

Three service templates fetched Firefox ESR from Mozilla's "latest" endpoint and
extracted it with ``tar -xjf``, which forces bzip2. Mozilla now serves that endpoint
as xz, so bzip2 rejected the file and the build died::

    tar: Child returned status 2
    tar: Error is not recoverable: exiting now

The endpoint has no version in it — ``?product=firefox-esr-latest-ssl`` — so what
arrives can change format without anything in this repository changing. Forcing a
decompressor on that is the defect; ``tar -xf`` reads the format from the file.

Found on 2026-09-16 by building every service image from scratch while verifying #472,
which is unrelated to it. Three of the six templates that fetch Firefox had already
been moved to xz and three had not, so this is also a half-finished fix: the guard
below covers the class rather than the three instances, because the next template
added by copy-paste would otherwise reintroduce it.

A pre-merge job builds only the chatbot slice — ``Dockerfile-chat``,
``Dockerfile-postgres``, ``Dockerfile-data-manager`` — and none of the six templates
that fetch this download is in that slice; they are ``Dockerfile-grader``,
``Dockerfile-grader-gpu``, ``Dockerfile-chat-gpu``, ``Dockerfile-data-manager-gpu``,
``Dockerfile-mattermost-gpu``, and ``Dockerfile-benchmarks-gpu``. These templates
had been unbuildable on ``dev`` for as long as Mozilla has served xz.

The contract (issue #519, operator decision of 2026-10-09; design D31 of
``openspec/changes/fix-issue-519-tar-guard-findings``). The guard reads a few simple
forms and fails closed on everything else, so review checks this list instead of
finding the next shell feature a hand-written parser misreads.

1. Instructions. Comment lines are dropped, then lines ending in ``\\`` are joined. Each
   line is one instruction; its first word, in any case, is the keyword.
2. These instructions fail closed: any instruction holding ``<<`` (a heredoc or
   here-string; the scan stops there and this entry cannot be allow-listed); ``RUN [``
   (exec form); a second ``FROM``; ``SHELL``; ``ADD`` of a URL; ``ENV`` or ``ARG``
   holding a moving URL, ``TAR_OPTIONS`` or ``TAPE``; a line that is not a Dockerfile
   instruction. Every other instruction except ``RUN`` is skipped; ``COPY`` and ``ADD``
   do not change what the guard knows about a path.
3. A shell-form ``RUN`` is read when it names ``tar``, ``wget`` or ``curl`` as a word (any
   case), or holds a moving URL, ``TAR_OPTIONS`` or ``TAPE``, in its raw text or in its
   words after quote removal (so ``t\\ar`` names tar). Other RUNs are skipped; a RUN
   that cannot be split into words fails closed. The ``RUN`` keyword and its ``--flag``
   words are dropped.
4. Words use ``'…'``, ``"…"`` and backslash escapes; an unquoted ``#`` at the start of a
   word starts a comment. ``&&``, ``;`` and ``|`` separate simple commands, read left to
   right. Output redirections (``>``, ``>>``, ``>|``, ``2>``, ``2>&1``, ``&>``) are
   allowed except on wget and curl. Fails closed: an unterminated quote, ``||``, ``&``,
   ``|&``, ``;;``, ``(``, ``)``, a backtick or ``$(``, and any input redirection.
5. Leading ``NAME=value`` words are assignments; the next word is the command. Fails
   closed: an assignment in front of tar, wget or curl; a command word holding ``$``; a
   reserved word or brace as the command; a shell, ``eval``, ``source`` or ``.``; any other
   command carrying a word named tar, wget, curl or a shell, unless it only prints or
   installs its arguments (``_PRINTS_OR_INSTALLS``); a moving URL outside wget and curl.
   Any other command is skipped.
6. tar. A first argument with no leading ``-`` is a traditional option word (letters
   only); each of its letters that takes an argument takes the next word, in letter
   order, and the words after are read as usual. Dash clusters: each letter must be a
   GNU tar 1.35 short option; one that takes an argument takes the rest of the cluster
   or the next word. Long options must be GNU tar 1.35 names spelled in full. ``--`` ends
   options. Exactly one operation mode; a write mode (``c r u A``, ``--delete``) is not
   an extraction. Fails closed: no mode, two modes, two ``-f``, an unknown or abbreviated
   option, and a forcing tar whose archive holds ``$``.
7. wget and curl. The output option and curl's per-transfer pairing are read as the
   #507 review rounds settled. Fails closed: any word holding ``$``.
8. Verdict. Writes are recorded in file order and the last write to a path wins. A
   forcing read-mode tar is reported when its archive held a moving download, or when
   it shares its RUN with a moving download and its archive is unknown (none, ``-``,
   ``/dev/stdout``). Paths are normalised; a relative path matches by basename.

An unparseable instruction is reported as ``unparseable command '…': <reason> — needs
review``. A human who reviewed it lists its exact text under its template in
``_REVIEWED_UNPARSEABLE``, with a comment saying why it is safe.
"""

import posixpath
import re
from typing import NamedTuple

import pytest

from src.cli.managers.base_image_preflight import service_templates

# EVERY tar option that forces a compression program, not just the one this defect
# happened to involve. Long forms are matched whole after stripping ``=PROG`` — so
# ``--no-auto-compress`` and ``--exclude=*.gz`` stay clean. Short letters are matched
# case-sensitively — ``-i`` (``--ignore-zeros``) differs from ``-I``
# (``--use-compress-program``) by case alone.
_FORCING_LONG = frozenset(
    {
        "--gzip",
        "--gunzip",
        "--ungzip",
        "--bzip2",
        "--xz",
        "--lzma",
        "--lzop",
        "--zstd",
        "--lzip",
        "--compress",
        "--uncompress",
        "--use-compress-program",
    }
)
_FORCING_SHORT = frozenset("zjJZI")
_STDOUT_SINKS = frozenset({"-", "/dev/stdout", "/dev/null"})

# tar's operation modes. A read mode is judged; a write mode names its ``-f`` archive as
# an output, so it is not an extraction (#519 row 2).
_READ_MODES = frozenset("xtd") | {"--test-label"}
_MODE_OF = {
    "--extract": "x",
    "--get": "x",
    "--list": "t",
    "--diff": "d",
    "--compare": "d",
    "--test-label": "--test-label",
    "--create": "c",
    "--append": "r",
    "--update": "u",
    "--catenate": "A",
    "--concatenate": "A",
    "--delete": "--delete",
}
_SHORT_MODES = frozenset("xtdcruA")

# GNU tar 1.35's options, measured from ``tar --help``. A short letter in
# ``_SHORT_WITH_ARGUMENT`` takes the rest of its cluster or the next word; ``-f`` names
# the archive and ``-I`` names a compression program. Review on 2026-09-19: without
# this set, ``tar -xf/tmp/firefox.tar.xz`` read the ``z`` in the FILENAME as forcing.
_SHORT_WITH_ARGUMENT = frozenset("bCfFgHIKLNTVX")
_SHORT_OPTIONS = _SHORT_WITH_ARGUMENT | frozenset("?ABGJMOPRSUWZacdhijklmnoprstuvwxz")

# Long options whose argument is REQUIRED, so it is the next word when no ``=`` is
# attached. Review on 2026-09-20: without this set, ``tar --exclude --gzip -xf
# /tmp/moving`` read the exclusion PATTERN as a forcing option.
_LONG_WITH_ARGUMENT = frozenset(
    """
    --add-file --after-date --blocking-factor --checkpoint-action --directory --exclude
    --exclude-from --exclude-ignore --exclude-ignore-recursive --exclude-tag
    --exclude-tag-all --exclude-tag-under --file --files-from --format --group
    --group-map --hole-detection --index-file --info-script --label --level
    --listed-incremental --mode --mtime --new-volume-script --newer --newer-mtime
    --no-quote-chars --owner --owner-map --pax-option --quote-chars --quoting-style
    --record-size --rmt-command --rsh-command --sort --sparse-version --starting-file
    --strip-components --suffix --tape-length --to-command --transform
    --use-compress-program --volno-file --warning --xattrs-exclude --xattrs-include
    --xform
    """.split()
)
# These take a value only when it is attached (``[=ARG]``).
_LONG_WITH_OPTIONAL_ARGUMENT = frozenset(
    "--atime-preserve --backup --checkpoint --occurrence --one-top-level --totals".split()
)
_LONG_FLAGS = frozenset(
    """
    --absolute-names --acls --anchored --append --auto-compress --block-number --bzip2
    --catenate --check-device --check-links --clamp-mtime --compare --compress
    --concatenate --confirmation --create --delay-directory-restore --delete
    --dereference --diff --exclude-backups --exclude-caches --exclude-caches-all
    --exclude-caches-under --exclude-vcs --exclude-vcs-ignores --extract --force-local
    --full-time --get --gunzip --gzip --hard-dereference --help --ignore-case
    --ignore-command-error --ignore-failed-read --ignore-zeros --incremental
    --interactive --keep-directory-symlink --keep-newer-files --keep-old-files --list
    --lzip --lzma --lzop --multi-volume --no-acls --no-anchored --no-auto-compress
    --no-check-device --no-delay-directory-restore --no-ignore-case
    --no-ignore-command-error --no-null --no-overwrite-dir --no-recursion
    --no-same-owner --no-same-permissions --no-seek --no-selinux --no-unquote
    --no-verbatim-files-from --no-wildcards --no-wildcards-match-slash --no-xattrs --null
    --numeric-owner --old-archive --one-file-system --overwrite --overwrite-dir
    --portability --posix --preserve-order --preserve-permissions --read-full-records
    --recursion --recursive-unlink --remove-files --restrict --same-order --same-owner
    --same-permissions --seek --selinux --show-defaults --show-omitted-dirs
    --show-snapshot-field-ranges --show-stored-names --show-transformed-names
    --skip-old-files --sparse --test-label --to-stdout --touch --uncompress --ungzip
    --unlink-first --unquote --update --usage --utc --verbatim-files-from --verbose
    --verify --version --wildcards --wildcards-match-slash --xattrs --xz --zstd
    """.split()
)

# The tools whose saved-output option tells the guard where a download landed, with the
# short LETTER and the long option that name that destination. Both accept the value
# attached to the short form: ``wget -O/tmp/x`` and ``curl -o/tmp/x`` are valid, and
# curl's own manual says a short option may be used "with or without a space between
# it and its value".
_DOWNLOAD_OUTPUT_OPTIONS = {
    "wget": ("O", "--output-document"),
    "curl": ("o", "--output"),
}

# Each tool's short options that take an argument: the rest of their own cluster when
# one is attached, otherwise the next token. Value-less flags may precede them in the
# same cluster — ``curl -sLo/tmp/x`` is ``-s -L -o/tmp/x`` and ``wget -qO /tmp/x`` is
# ``-q -O /tmp/x``. Measured from ``curl --help all`` (curl 8.21.0; ``-h`` omitted, its
# subject is optional) and ``wget --help`` (GNU Wget 1.25.0). Review on 2026-09-20:
# matching the token prefix ``-o`` missed every clustered spelling, and the saved path
# was lost silently.
_DOWNLOAD_SHORT_WITH_ARGUMENT = {
    "wget": frozenset("aABDeiIloOPQRtTUwX"),
    "curl": frozenset("AbcCdDeEFHKmoPQrtTuUwxXyYz"),
}

# curl's long options by arity, measured from ``curl --help all`` (curl 8.21.0): the
# entries shown with an ``<argument>`` take one, the rest take none (``--help`` omitted,
# its subject is optional). ``--output``, ``--remote-name`` and ``--url`` name a
# transfer's destination or the transfer itself and are handled by name first.
# Adversarial pass on 2026-09-20: ``curl --header https://… -o /tmp/moving <latest>``
# paired the header's URL-shaped value with ``-o`` and read the moving destination as
# pinned. A long option in neither table has unknown arity, which disables the pairing.
_CURL_LONG_WITH_ARGUMENT = frozenset(
    """
    --abstract-unix-socket --alt-svc --aws-sigv4 --cacert --capath --cert --cert-type
    --ciphers --config --connect-timeout --connect-to --continue-at --cookie --cookie-jar
    --create-file-mode --crlfile --curves --data --data-ascii --data-binary --data-raw
    --data-urlencode --delegation --dns-interface --dns-ipv4-addr --dns-ipv6-addr
    --dns-servers --doh-url --dump-header --ech --egd-file --engine --etag-compare
    --etag-save --expect100-timeout --form --form-string --ftp-account
    --ftp-alternative-to-user --ftp-method --ftp-port --ftp-ssl-ccc-mode
    --happy-eyeballs-timeout-ms --haproxy-clientip --header --hostpubmd5 --hostpubsha256
    --hsts --interface --ipfs-gateway --ip-tos --json --keepalive-cnt --keepalive-time
    --key --key-type --knownhosts --krb --libcurl --limit-rate --local-port
    --login-options --mail-auth --mail-from --mail-rcpt --max-filesize --max-redirs
    --max-time --netrc-file --noproxy --oauth2-bearer --output --output-dir
    --parallel-max --parallel-max-host --pass --pinnedpubkey --preproxy --proto
    --proto-default --proto-redir --proxy --proxy1.0 --proxy-cacert --proxy-capath
    --proxy-cert --proxy-cert-type --proxy-ciphers --proxy-crlfile --proxy-header
    --proxy-key --proxy-key-type --proxy-pass --proxy-pinnedpubkey --proxy-service-name
    --proxy-tls13-ciphers --proxy-tlsauthtype --proxy-tlspassword --proxy-tlsuser
    --proxy-user --pubkey --quote --random-file --range --rate --referer --request
    --request-target --resolve --retry --retry-delay --retry-max-time --sasl-authzid
    --service-name --sigalgs --socks4 --socks4a --socks5 --socks5-gssapi-service
    --socks5-hostname --speed-limit --speed-time --ssl-sessions --stderr --telnet-option
    --tftp-blksize --time-cond --tls13-ciphers --tlsauthtype --tls-max --tlspassword
    --tlsuser --trace --trace-ascii --trace-config --unix-socket --upload-file
    --upload-flags --url --url-query --user --user-agent --variable --vlan-priority
    --write-out
    """.split()
)
_CURL_LONG_FLAGS = frozenset(
    """
    --anyauth --append --basic --ca-native --cert-status --compressed --compressed-ssh
    --create-dirs --crlf --digest --disable --disable-eprt --disable-epsv
    --disallow-username-in-url --doh-cert-status --doh-insecure --dump-ca-embed --fail
    --fail-early --fail-with-body --false-start --follow --form-escape --ftp-create-dirs
    --ftp-pasv --ftp-pret --ftp-skip-pasv-ip --ftp-ssl-ccc --ftp-ssl-control --get
    --globoff --haproxy-protocol --head --http0.9 --http1.0 --http1.1 --http2
    --http2-prior-knowledge --http3 --http3-only --ignore-content-length --insecure
    --ipv4 --ipv6 --junk-session-cookies --list-only --location --location-trusted
    --mail-rcpt-allowfails --manual --metalink --mptcp --negotiate --netrc
    --netrc-optional --no-alpn --no-buffer --no-clobber --no-keepalive --no-npn
    --no-progress-meter --no-sessionid --ntlm --ntlm-wb --out-null --parallel
    --parallel-immediate --path-as-is --post301 --post302 --post303 --progress-bar
    --proxy-anyauth --proxy-basic --proxy-ca-native --proxy-digest --proxy-http2
    --proxy-http3 --proxy-insecure --proxy-negotiate --proxy-ntlm --proxy-ssl-allow-beast
    --proxy-ssl-auto-client-cert --proxy-tlsv1 --proxytunnel --raw --remote-header-name
    --remote-name --remote-name-all --remote-time --remove-on-error --retry-all-errors
    --retry-connrefused --sasl-ir --show-error --show-headers --silent --skip-existing
    --socks5-basic --socks5-gssapi --socks5-gssapi-nec --ssl --ssl-allow-beast
    --ssl-auto-client-cert --ssl-no-revoke --ssl-reqd --ssl-revoke-best-effort --sslv2
    --sslv3 --styled-output --suppress-connect-headers --tcp-fastopen --tcp-nodelay
    --tftp-no-options --tls-earlydata --tlsv1 --tlsv1.0 --tlsv1.1 --tlsv1.2 --tlsv1.3
    --trace-ids --trace-time --tr-encoding --use-ascii --verbose --version --xattr
    """.split()
)

# A bare word curl or wget would read as a URL. The scheme identifies it; a bare word
# without one — an unmodelled option's argument — means the guard cannot pair outputs
# with transfers and reads the invocation whole.
_URL_LIKE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
# A download URL that names no version, so its payload can change under us.
_MOVING_DOWNLOAD = re.compile(r"download\.mozilla\.org|[?&]product=[^\s\"']*latest")

_NEEDS_REVIEW = "needs review"

# Instructions a human reviewed and that the guard does not read. Keyed by template file
# name; each entry is the instruction exactly as ``_commands`` returns it (continuation
# lines joined), so any edit to it needs a new review. Every entry carries a comment
# saying why it is safe. An entry must not hold a moving URL, and a stale entry fails
# ``test_every_reviewed_entry_is_live_and_holds_no_moving_url``.
#
# Reviewed 2026-10-09 (#519): the geckodriver step. It fetches the version-pinned
# geckodriver v0.36.0 (the version is set in the same RUN) and extracts that file with
# ``-xzf``, which is correct for a pinned payload. The ``if`` only picks the CPU
# architecture. It is unparseable because of the ``if`` and the ``$`` in the wget URL.
_GECKODRIVER_BY_ARCHITECTURE = (
    'RUN GECKO_VERSION="v0.36.0" && if [ "$TARGETARCH" = "arm64" ]; then '
    'GECKO_ARCH="linux-aarch64"; else GECKO_ARCH="linux64"; fi && wget -q '
    '"https://github.com/mozilla/geckodriver/releases/download/${GECKO_VERSION}/'
    'geckodriver-${GECKO_VERSION}-${GECKO_ARCH}.tar.gz" && tar -xzf '
    '"geckodriver-${GECKO_VERSION}-${GECKO_ARCH}.tar.gz" -C /usr/local/bin && '
    'chmod +x /usr/local/bin/geckodriver && rm "geckodriver-${GECKO_VERSION}-'
    '${GECKO_ARCH}.tar.gz"'
)
_REVIEWED_UNPARSEABLE: dict[str, tuple[str, ...]] = {
    "Dockerfile-chat": (_GECKODRIVER_BY_ARCHITECTURE,),
    "Dockerfile-data-manager": (_GECKODRIVER_BY_ARCHITECTURE,),
    "Dockerfile-data-manager-gpu": (_GECKODRIVER_BY_ARCHITECTURE,),
}

_DOCKERFILE_KEYWORDS = frozenset(
    """
    ADD ARG CMD COPY ENTRYPOINT ENV EXPOSE FROM HEALTHCHECK LABEL MAINTAINER ONBUILD RUN
    SHELL STOPSIGNAL USER VOLUME WORKDIR
    """.split()
)
# A RUN is read when it could fetch or extract through the programs the guard knows.
_READ_RUN = re.compile(r"\b(?:tar|wget|curl)\b|TAR_OPTIONS|\bTAPE\b", re.IGNORECASE)
# Variables that change what tar reads or how; the guard does not follow them.
_TAR_ENVIRONMENT = re.compile(r"TAR_OPTIONS|\bTAPE\b")
_RUN_PREFIX = re.compile(r"^\s*RUN\b(?:\s+--\S+)*\s*", re.IGNORECASE)

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_RESERVED_WORDS = frozenset(
    """
    if then else elif fi for while until do done case esac select function coproc time
    in { } ! [[ ]]
    """.split()
)
_SHELLS = frozenset({"sh", "bash", "dash", "ash", "zsh"})
_RUNS_A_SCRIPT = _SHELLS | {"eval", "source", "."}
# A word with one of these names, given to any other command, may be run by it.
_PROGRAM_WORDS = frozenset({"tar", "wget", "curl"}) | _SHELLS
# Commands that only print or install their arguments, so a program name there is data.
_PRINTS_OR_INSTALLS = frozenset(
    {"echo", "printf", "apt-get", "apt", "apk", "dnf", "yum", "microdnf"}
)
_OUTPUT_REDIRECTION = re.compile(r"^(?:\d*(?:>>|>\||>&|>)(?:\d+|-)?|&>>?)$")
_REDIRECTION_DUP = re.compile(r"&(?:\d+|-)$")
_SEPARATORS = frozenset({"&&", ";", "|"})


class _Unreadable(ValueError):
    """A form outside the contract. The message says which form."""


class _Operator(str):
    """A token the shell reads as an operator, as opposed to a word that spells one.

    ``echo ';'`` passes a word to echo; the ``;`` in ``a;b`` ends a command. Both are
    the string ``;`` once the quotes are resolved, so the lexer marks the operator.
    """


class _Download(NamedTuple):
    """One wget or curl invocation: what it wrote, and its arguments for the URL check."""

    writes: dict  # saved path -> True when the transfer that wrote it is moving
    text: str


def _shell_tokens(command: str) -> list[str]:
    """``command`` split into words and operators the way the shell splits it.

    Words are delimited by unquoted whitespace, with quotes resolved: ``"/tmp/my
    moving.tar"``, ``'/tmp/my moving.tar'`` and ``/tmp/my\\ moving.tar`` are all the
    one word ``/tmp/my moving.tar``. Control operators (``&&``, ``||``, ``;``, ``|``,
    ``&``, ``(``, ``)``) and redirection operators (``>``, ``>>``, ``<``, ``2>&1``,
    ``&>`` …) are isolated whether or not whitespace surrounds them, and returned as
    :class:`_Operator` so a quoted word that spells one is not mistaken for one. An
    unquoted ``#`` at the start of a word begins a comment.

    Review on 2026-09-19: ``wget …&&tar …`` hid both the URL and the tar token, so a
    forced extraction of a moving download read as clean. Review on 2026-09-20:
    ``str.split`` broke a quoted path with a space into two words, and
    ``tar -xzf /tmp/moving>/dev/null`` kept the redirection glued to the archive, so
    the saved path stopped matching and the forced decompressor escaped.

    Raises ``ValueError`` on an unterminated quote. The guard cannot say which file
    such a command reads, and :func:`_offenders` reports that rather than passing it.
    """
    tokens: list[str] = []
    word: list[str] = []
    in_word = False
    i = 0
    n = len(command)

    def flush() -> None:
        nonlocal in_word
        if in_word:
            tokens.append("".join(word))
        word.clear()
        in_word = False

    while i < n:
        c = command[i]
        if c in " \t\n":
            flush()
            i += 1
        elif c == "#" and not in_word:
            break
        elif c == "'":
            end = command.find("'", i + 1)
            if end < 0:
                raise ValueError("unterminated single quote")
            word.append(command[i + 1 : end])
            in_word = True
            i = end + 1
        elif c == '"':
            i += 1
            while i < n and command[i] != '"':
                if command[i] == "\\" and i + 1 < n and command[i + 1] in '"$`\\':
                    i += 1
                word.append(command[i])
                i += 1
            if i >= n:
                raise ValueError("unterminated double quote")
            in_word = True
            i += 1
        elif c == "\\":
            if i + 1 < n:
                word.append(command[i + 1])
                in_word = True
            i += 2
        elif c in "<>":
            # Digits immediately in front of the operator are its descriptor: ``2>``.
            descriptor = ""
            if in_word and "".join(word).isdigit():
                descriptor = "".join(word)
                word.clear()
                in_word = False
            flush()
            operator = c
            j = i + 1
            if j < n and command[j] in (">&|" if c == ">" else "<&>"):
                operator += command[j]
                j += 1
                if operator == "<<" and j < n and command[j] == "<":
                    operator += "<"
                    j += 1
            if operator.endswith("&"):
                dup = re.match(r"(\d+|-)(?=$|[\s;&|<>()])", command[j:])
                if dup:
                    operator += dup.group(1)
                    j += dup.end()
            tokens.append(_Operator(descriptor + operator))
            i = j
        elif c in "&|;":
            flush()
            if command.startswith("&>>", i):
                operator = "&>>"
            elif command[i : i + 2] in ("&&", "&>", "||", "|&", ";;"):
                operator = command[i : i + 2]
            else:
                operator = c
            tokens.append(_Operator(operator))
            i += len(operator)
        elif c == "$" and i + 1 < n and command[i + 1] == "(":
            # A command substitution is one word, up to its matching parenthesis.
            depth = 0
            j = i + 1
            while j < n:
                if command[j] == "(":
                    depth += 1
                elif command[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j >= n:
                raise ValueError("unterminated command substitution")
            word.append(command[i : j + 1])
            in_word = True
            i = j + 1
        elif c == "`":
            end = command.find("`", i + 1)
            if end < 0:
                raise ValueError("unterminated backtick substitution")
            word.append(command[i : end + 1])
            in_word = True
            i = end + 1
        elif c in "()":
            flush()
            tokens.append(_Operator(c))
            i += 1
        else:
            word.append(c)
            in_word = True
            i += 1
    flush()
    return tokens


def _simple_commands(command: str) -> list[tuple[list[str], bool]]:
    """``command`` as the shell's simple commands: ``(argv, has an output redirection)``.

    Only ``&&``, ``;`` and ``|`` separate commands, and only output redirections are
    read; their target never reaches the program's argv, so ``tar -xzf
    /tmp/moving>/dev/null`` hands tar exactly ``-xzf /tmp/moving``. Every other operator,
    and a command substitution, is outside the contract.
    """
    try:
        tokens = _shell_tokens(command)
    except ValueError as exc:
        raise _Unreadable(str(exc)) from exc
    commands: list[tuple[list[str], bool]] = []
    argv: list[str] = []
    redirected = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not isinstance(token, _Operator):
            if "$(" in token or "`" in token:
                raise _Unreadable(f"command substitution in {token!r}")
            argv.append(token)
        elif token in _SEPARATORS:
            if argv:
                commands.append((argv, redirected))
            argv, redirected = [], False
        elif _OUTPUT_REDIRECTION.match(token):
            redirected = True
            if not _REDIRECTION_DUP.search(token):
                i += 1  # the target word
        else:
            raise _Unreadable(f"shell operator {str(token)!r}")
        i += 1
    if argv:
        commands.append((argv, redirected))
    return commands


def _basename(token: str) -> str:
    """The command name of ``token``, so ``/bin/tar`` is recognised as ``tar``."""
    return token.rsplit("/", 1)[-1]


def _parse_tar_span(span: list[str]) -> tuple[list[str], str | None]:
    """One tar invocation's forcing options and the archive it reads (contract item 6).

    The archive comes from ``-f``/``--file`` only; without it tar reads stdin or its
    default device. A write mode returns ``([], None)``: its archive is an output.
    Raises :class:`_Unreadable` for a form outside the contract.
    """
    words = list(span)
    forcing: list[str] = []
    archives: list[str] = []
    modes: set[str] = set()

    def read_letters(token: str, letters: str, take) -> None:
        for position, letter in enumerate(letters):
            if letter not in _SHORT_OPTIONS:
                raise _Unreadable(
                    f"tar option -{letter} in {token!r} is not a tar option"
                )
            if letter in _SHORT_MODES:
                modes.add(letter)
            if letter in _FORCING_SHORT and token not in forcing:
                forcing.append(token)
            if letter in _SHORT_WITH_ARGUMENT:
                value = take(letters[position + 1 :])
                if letter == "f":
                    archives.append(value)
                if token.startswith("-"):
                    return  # the rest of a dash cluster is this option's argument

    def next_word(attached: str = "") -> str:
        if attached:
            return attached
        if not words:
            raise _Unreadable("a tar option is missing its argument")
        return words.pop(0)

    if words and not words[0].startswith("-"):
        # A traditional option word: its letters' arguments follow it in letter order.
        token = words.pop(0)
        if not token.isalpha() or not token.isascii():
            raise _Unreadable(f"tar's first argument {token!r} is not an option word")
        read_letters(token, token, lambda _rest: next_word())
    end_of_options = False
    while words:
        token = words.pop(0)
        if end_of_options or token == "-" or not token.startswith("-"):
            continue
        if token == "--":
            # Everything after tar's end-of-options marker is an operand.
            end_of_options = True
        elif token.startswith("--"):
            name, separator, value = token.partition("=")
            if name in _LONG_WITH_ARGUMENT:
                value = value if separator else next_word()
            elif name not in _LONG_WITH_OPTIONAL_ARGUMENT and (
                name not in _LONG_FLAGS or separator
            ):
                raise _Unreadable(f"tar option {token!r} is not a full GNU tar name")
            if name in _MODE_OF:
                modes.add(_MODE_OF[name])
            if name in _FORCING_LONG:
                forcing.append(token)
            if name == "--file":
                archives.append(value)
        else:
            read_letters(token, token[1:], next_word)
    if len(modes) != 1:
        raise _Unreadable(f"tar needs one operation mode, got {sorted(modes)}")
    if len(archives) > 1:
        raise _Unreadable("tar names more than one archive")
    if not modes <= _READ_MODES:
        return [], None
    archive = archives[0] if archives else None
    if forcing and archive is not None and "$" in archive:
        raise _Unreadable(f"tar's archive {archive!r} is a variable")
    return forcing, archive


def _named_commands(command: str):
    """``(name, arguments)`` for each tar, wget or curl the RUN's shell runs (item 5).

    Raises :class:`_Unreadable` for a form outside the contract.
    """
    command = _RUN_PREFIX.sub("", command, count=1)
    for argv, redirected in _simple_commands(command):
        position = 0
        while position < len(argv) and _ASSIGNMENT.match(argv[position]):
            position += 1
        if position == len(argv):
            continue  # assignments only
        word, arguments = argv[position], argv[position + 1 :]
        name = _basename(word)
        if "$" in word:
            raise _Unreadable(f"the command {word!r} is a variable")
        if word in _RESERVED_WORDS:
            raise _Unreadable(f"the shell keyword {word!r}")
        if name in _RUNS_A_SCRIPT:
            raise _Unreadable(f"{name} runs a script the guard does not read")
        if name in ("tar", "wget", "curl"):
            if position:
                raise _Unreadable(f"an assignment in front of {name}")
            if name != "tar" and redirected:
                raise _Unreadable(f"{name} with a redirection")
            if name != "tar" and any("$" in argument for argument in arguments):
                raise _Unreadable(f"{name} with a variable argument")
            yield name, arguments
            continue
        if name not in _PRINTS_OR_INSTALLS and any(
            _basename(argument) in _PROGRAM_WORDS for argument in arguments
        ):
            raise _Unreadable(f"{name} may run a program it is given")
        if any(_MOVING_DOWNLOAD.search(argument) for argument in arguments):
            raise _Unreadable(f"a moving URL given to {name}")


def _tar_invocations(command: str) -> list:
    """Every tar invocation in ``command`` as ``(forcing options, archive)``."""
    return [
        _parse_tar_span(arguments)
        for name, arguments in _named_commands(command)
        if name == "tar"
    ]


def _forced_decompressors(command: str) -> list[str]:
    """Every forcing option of every tar invocation in ``command``."""
    return [option for forcing, _ in _tar_invocations(command) for option in forcing]


class _ForcedDecompressorScanner:
    def findall(self, command: str) -> list[str]:
        return _forced_decompressors(command)


_FORCED_DECOMPRESSOR = _ForcedDecompressorScanner()


def _templates():
    found = service_templates()
    assert found, "no service templates found; base_image_preflight may have moved"
    return sorted(found)


def _commands(text: str) -> list[str]:
    """``text`` as Dockerfile instructions: comment lines dropped, continuations joined.

    The pairing matters and is why this is not a whole-file scan. Forcing a
    decompressor is only wrong on a MOVING download: these templates also fetch
    geckodriver from a URL that names ``v0.36.0``, where ``tar -xzf`` is correct
    because the payload cannot change format underneath it. A file-wide regex flagged
    that legitimate line as soon as the decompressor check was widened past bzip2, so
    the check has to see which download each extraction belongs to.
    """
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    joined = re.sub(r"[ \t]*\\[ \t]*\n\s*", " ", "\n".join(lines))
    return [line.strip() for line in joined.splitlines() if line.strip()]


def _parse_download(name: str, span: list[str]) -> _Download:
    """One wget or curl invocation's writes, each paired with the transfer that made it.

    Every spelling of the destination option is read: spaced (``-O /tmp/x``), attached
    short (``-O/tmp/x``), clustered (``-qO /tmp/x``, ``-sLo/tmp/x``), spaced long
    (``--output-document /tmp/x``) and attached long (``--output-document=/tmp/x``).
    Round 2 on 2026-09-19 found the attached long form missing, which loses the saved
    path silently — and branch 2 only reaches within the download's own command, so a
    forced extraction in a LATER RUN then read as clean.

    wget's ``-O`` names ONE file for every URL of the invocation, so that file is
    moving when any URL is. curl pairs its ``-o`` options with its URLs in order —
    ``curl --manual``: the first ``-o`` corresponds to the first URL — so
    ``curl -o /tmp/moving <latest> -o /tmp/pinned <versioned>`` marks only
    ``/tmp/moving``; curl's ``-O`` is a transfer whose destination the guard does not
    know. Review on 2026-09-20: both destinations were marked from the invocation's
    whole text. The pairing is trusted only when every bare word is a URL; otherwise
    the invocation is read whole, the conservative reading the guard used before.
    """
    output_letter, long_form = _DOWNLOAD_OUTPUT_OPTIONS[name]
    with_argument = _DOWNLOAD_SHORT_WITH_ARGUMENT[name]
    remote_name = "O" if name == "curl" else None
    long_with_argument = _CURL_LONG_WITH_ARGUMENT if name == "curl" else frozenset()
    long_flags = _CURL_LONG_FLAGS if name == "curl" else frozenset()
    # One entry per transfer that names a destination; None = a destination unknown.
    outputs: list = []
    urls: list[str] = []
    paired = True

    def _url(word: str) -> None:
        nonlocal paired
        urls.append(word)
        paired = paired and bool(_URL_LIKE.match(word))

    i = 0
    while i < len(span):
        word = span[i]
        if word == long_form:
            if i + 1 < len(span):
                i += 1
                outputs.append(span[i])
        elif word.startswith(f"{long_form}="):
            outputs.append(word[len(long_form) + 1 :])
        elif word == "--remote-name":
            outputs.append(None)
        elif word == "--url":
            if i + 1 < len(span):
                i += 1
                _url(span[i])
        elif word.startswith("--url="):
            _url(word[len("--url=") :])
        elif word.startswith("--"):
            # A long option of known arity is consumed with its argument. One the
            # tables do not know may or may not have taken the next word, so the
            # pairing cannot be trusted.
            option = word.partition("=")[0]
            if "=" in word or option in long_flags:
                pass
            elif option in long_with_argument:
                if i + 1 < len(span):
                    i += 1
            else:
                paired = False
        elif word.startswith("-") and word != "-":
            # A short-option cluster: value-less flags until the first option that
            # takes an argument, which consumes the rest of the cluster or, when
            # nothing is attached, the next word.
            cluster = word[1:]
            for offset, character in enumerate(cluster):
                if character == remote_name:
                    outputs.append(None)
                if character not in with_argument:
                    continue
                attached = cluster[offset + 1 :]
                if character == output_letter:
                    if attached:
                        outputs.append(attached)
                    elif i + 1 < len(span):
                        i += 1
                        outputs.append(span[i])
                elif not attached and i + 1 < len(span):
                    i += 1
                break
        elif not word.startswith("-"):
            _url(word)
        i += 1
    text = " ".join(span)
    if name == "curl" and paired:
        transfers = [
            (path, bool(_MOVING_DOWNLOAD.search(url)))
            for path, url in zip(outputs, urls)
        ]
    else:
        whole = bool(_MOVING_DOWNLOAD.search(text))
        transfers = [(path, whole) for path in outputs]
    writes = {
        path: moving
        for path, moving in transfers
        if path is not None and path not in _STDOUT_SINKS
    }
    return _Download(writes, text)


def _download_invocations(command: str) -> list:
    """Every wget or curl invocation in ``command`` as a :class:`_Download`."""
    return [
        _parse_download(name, arguments)
        for name, arguments in _named_commands(command)
        if name in _DOWNLOAD_OUTPUT_OPTIONS
    ]


def _invocations(command: str) -> list:
    """Every tar and download invocation of ``command``, in the order the shell runs them.

    A tar invocation is its ``(forcing options, archive)`` pair; a download is a
    :class:`_Download`. Raises :class:`_Unreadable` before returning anything, so a
    command outside the contract contributes nothing.
    """
    return [
        (
            _parse_tar_span(arguments)
            if name == "tar"
            else _parse_download(name, arguments)
        )
        for name, arguments in _named_commands(command)
    ]


def _instruction_invocations(instruction: str, keyword: str) -> list:
    """The invocations an instruction runs (items 2 and 3); ``[]`` when it runs none."""
    if keyword == "SHELL":
        raise _Unreadable("SHELL changes the shell RUN uses")
    if keyword == "ADD" and "://" in instruction:
        raise _Unreadable("ADD fetches a URL")
    if keyword in ("ENV", "ARG") and (
        _MOVING_DOWNLOAD.search(instruction) or _TAR_ENVIRONMENT.search(instruction)
    ):
        raise _Unreadable(f"{keyword} sets a value the guard does not follow")
    if keyword != "RUN":
        return []
    command = _RUN_PREFIX.sub("", instruction, count=1)
    if command.startswith("["):
        raise _Unreadable("exec-form RUN")
    try:
        # Relevance is decided on the words the shell reads, so ``t\ar`` and
        # ``t'a'r`` name tar (PR #626). A RUN that cannot be split into words is read,
        # and so fails closed.
        words = [command] + _shell_tokens(command)
    except ValueError as exc:
        raise _Unreadable(str(exc)) from exc
    if not any(_READ_RUN.search(w) or _MOVING_DOWNLOAD.search(w) for w in words):
        return []
    return _invocations(command)


def _normalised(path: str) -> str:
    return posixpath.normpath(path) if path else path


def _archive_matches_saved(archive, saved: set) -> bool:
    """True when the archive IS a saved path: equal once normalised, or, when either is
    relative, equal by basename (#519 row 11: ``cd /tmp && wget -O a …``).

    Whole-value comparison, not containment. Review on 2026-09-19: ``path in token``
    made the saved ``/tmp/a`` match a pinned ``/tmp/archive-v1.tar.gz``, and the
    basename branch made ``a`` match ``a.tar.old``.
    """
    if archive is None:
        return False
    reference = _normalised(archive)
    for path in saved:
        if reference == path:
            return True
        relative = not reference.startswith("/") or not path.startswith("/")
        if relative and posixpath.basename(reference) == posixpath.basename(path):
            return True
    return False


def _archive_is_unresolvable(archive) -> bool:
    """True when the guard cannot tell which file this tar reads: no ``-f`` (stdin or
    the default device) or a standard stream."""
    return archive is None or archive in _STDOUT_SINKS


def _unparseable(instruction: str, reason: str) -> str:
    return f"unparseable command {instruction!r}: {reason} — {_NEEDS_REVIEW}"


def _offenders(text: str, reviewed=()) -> list:
    """Forcing options on tar invocations that extract a moving download, and an
    ``unparseable … needs review`` entry for each instruction outside the contract
    that is not in ``reviewed``.

    Provenance follows Dockerfile order: each download's writes are recorded as the
    shell reaches them, a later write to the same path replaces the earlier one, and a
    tar is judged by what its archive held at that moment. Review on 2026-09-20: one
    file-wide set collected before any command was scanned let a LATER moving write
    indict an EARLIER extraction, and never let a pinned write clear a moving path.

    Three branches decide each forcing invocation:
    1. Its archive is a path a moving download had written by then — indict.
    2. The invocation shares a RUN with a moving download and the guard cannot
       resolve which file it reads — indict conservatively.
    3. Otherwise — clean.
    """
    result: list[str] = []
    provenance: dict = {}  # normalised saved path -> True while it holds a moving file
    stages = 0
    for instruction in _commands(text):
        if "<<" in instruction:
            result.append(
                _unparseable(
                    instruction, "a heredoc or here-string; nothing after it is read"
                )
            )
            break
        keyword = instruction.split(None, 1)[0].upper()
        stages += keyword == "FROM"
        if instruction in reviewed:
            continue
        try:
            if keyword not in _DOCKERFILE_KEYWORDS:
                raise _Unreadable("not a Dockerfile instruction")
            if keyword == "FROM" and stages > 1:
                raise _Unreadable("a second FROM starts another build stage")
            invocations = _instruction_invocations(instruction, keyword)
        except _Unreadable as exc:
            result.append(_unparseable(instruction, str(exc)))
            continue
        downloads = [found for found in invocations if isinstance(found, _Download)]
        is_moving = any(_MOVING_DOWNLOAD.search(found.text) for found in downloads)
        command_saved = {
            path
            for found in downloads
            for path, moving in found.writes.items()
            if moving
        }
        for invocation in invocations:
            if isinstance(invocation, _Download):
                for path, moving in invocation.writes.items():
                    provenance[_normalised(path)] = moving
                continue
            forcing, archive = invocation
            if not forcing:
                continue
            moving_paths = {path for path, moving in provenance.items() if moving}
            if _archive_matches_saved(archive, moving_paths):
                result.extend(forcing)
            elif is_moving and (not command_saved or _archive_is_unresolvable(archive)):
                result.extend(forcing)
    return result


@pytest.mark.parametrize("template", _templates(), ids=lambda p: p.name)
def test_a_moving_download_is_not_extracted_with_a_forced_decompressor(template):
    """A versionless download must be extracted with format auto-detection."""
    content = template.read_text(encoding="utf-8")
    if not _MOVING_DOWNLOAD.search(content):
        pytest.skip(f"{template.name} fetches no versionless archive")

    forced = _offenders(content, reviewed=_REVIEWED_UNPARSEABLE.get(template.name, ()))
    assert not forced, (
        f"{template.name} extracts a versionless download with {forced}, which forces "
        f"a compression program. The endpoint carries no version, so its payload can "
        f"change format without anything here changing — pinning the decompressor is "
        f"the defect whichever format is pinned. Mozilla serves xz today, so `-xjf` "
        f"failed with 'tar: Child returned status 2'; `-xzf` would fail the same way. "
        f"Use `tar -xf` and let tar detect the format."
    )


@pytest.mark.parametrize("template", _templates(), ids=lambda p: p.name)
def test_the_saved_filename_does_not_claim_a_format_it_cannot_guarantee(template):
    """Saving a versionless download as ``.tar.bz2`` misstates what arrived.

    This is cosmetic on its own — ``tar -xf`` ignores the name — but a wrong extension
    is what invites the next person to add a matching ``-xjf``, which is exactly how
    this defect survived in half the templates.
    """
    content = template.read_text(encoding="utf-8")
    if not _MOVING_DOWNLOAD.search(content):
        pytest.skip(f"{template.name} fetches no versionless archive")

    claimed = [
        command
        for command in _commands(content)
        if _MOVING_DOWNLOAD.search(command) and ".tar.bz2" in command
    ]
    assert not claimed, (
        f"{template.name} saves a versionless download as .tar.bz2, but the endpoint "
        f"serves xz. Name it .tar.xz so the file and the extraction agree."
    )


class TestTheGuardRejectsEveryForcedDecompressor:
    """The invariant is auto-detection, so every forcing option must trip it.

    Review on 2026-09-16: the guard claimed auto-detection but recognised only bzip2,
    so an edit to ``tar -xzf`` on the same moving URL would have stayed green while
    failing the image exactly as ``-xjf`` did.
    """

    @pytest.mark.parametrize(
        "command",
        [
            "tar -xjf /tmp/f.tar.bz2 -C /opt/",
            "tar -xzf /tmp/f.tar.gz -C /opt/",
            "tar -xJf /tmp/f.tar.xz -C /opt/",
            "tar -xZf /tmp/f.tar.Z -C /opt/",
            "tar --gzip -xf /tmp/f -C /opt/",
            "tar --bzip2 -xf /tmp/f -C /opt/",
            "tar --xz -xf /tmp/f -C /opt/",
            "tar --zstd -xf /tmp/f -C /opt/",
            "tar --lzma -xf /tmp/f -C /opt/",
            "tar -x --gzip -f /tmp/f",
            "tar -x -z -f /tmp/f",
            "tar --lzip -xf /tmp/f",
            "tar --uncompress -xf /tmp/f",
            "tar -I zstd -xf /tmp/f",
            "tar --use-compress-program=zstd -xf /tmp/f",
        ],
    )
    def test_a_forced_format_is_detected(self, command):
        assert _FORCED_DECOMPRESSOR.findall(command), (
            f"{command!r} forces a compression program and must be rejected on a "
            f"versionless download; the guard's invariant is auto-detection."
        )

    @pytest.mark.parametrize(
        "command",
        [
            "tar -xf /tmp/f.tar.xz -C /opt/",
            "tar -xvf /tmp/f.tar -C /opt/",
            "tar --extract --file /tmp/f -C /opt/",
        ],
    )
    def test_auto_detection_is_left_alone(self, command):
        assert not _FORCED_DECOMPRESSOR.findall(
            command
        ), f"{command!r} lets tar detect the format and must not trip the guard."

    @pytest.mark.parametrize(
        "command",
        [
            "tar -a -xf /tmp/f.tar.gz",
            "tar --auto-compress -xf /tmp/f",
            "tar --no-auto-compress -xf /tmp/f",
            "tar -xif /tmp/f.tar",
            "tar --exclude=*.gz -xf /tmp/f",
        ],
    )
    def test_near_misses_are_not_flagged(self, command):
        assert not _FORCED_DECOMPRESSOR.findall(
            command
        ), f"{command!r} does not force a decompressor and must not trip the guard."

    def test_separator_bounds_each_tar_invocation(self):
        """A shell separator ends a tar invocation; adjacent commands cannot bleed."""
        # A command after the separator must not contaminate the preceding clean tar.
        assert not _forced_decompressors(
            "tar -xf /tmp/f.tar.xz -C /opt/ && gzip -d /tmp/other.gz"
        ), "gzip after && must not be attributed to the preceding tar scan"
        # A second tar after the separator is scanned independently and indicted on its own.
        offenders = _forced_decompressors(
            "tar -xf /tmp/a.tar && tar -xzf /tmp/b.tar.gz"
        )
        assert len(offenders) == 1, (
            f"exactly one forcing option expected from the second tar invocation; "
            f"got {offenders}"
        )


class TestAVersionedDownloadMayForceItsFormat:
    """A pinned URL cannot change format underneath us, so forcing is fine there.

    These templates fetch geckodriver from a URL naming ``v0.36.0`` and extract it with
    ``tar -xzf``. That is correct, and a file-wide scan wrongly condemned it once the
    decompressor check widened past bzip2 — which is what forced the per-command pairing
    in ``_commands``.
    """

    def test_a_pinned_url_with_a_forced_format_is_accepted(self):
        command = (
            'RUN wget -q "https://github.com/mozilla/geckodriver/releases/download/'
            'v0.36.0/geckodriver-v0.36.0-linux64.tar.gz" && '
            'tar -xzf "geckodriver-v0.36.0-linux64.tar.gz" -C /usr/local/bin'
        )
        assert not _MOVING_DOWNLOAD.search(command), (
            "a URL naming v0.36.0 is not a moving download, so the guard must not "
            "reach its forced -xzf at all"
        )

    def test_the_two_are_not_paired_across_separate_commands(self):
        """A moving download in one command must not indict another command's format."""
        text = (
            "RUN wget -O /tmp/f.tar.xz "
            '"https://download.mozilla.org/?product=firefox-esr-latest-ssl" && \\\n'
            "    tar -xf /tmp/f.tar.xz -C /opt/\n"
            'RUN wget -q "https://example.invalid/tool-v1.2.3.tar.gz" && \\\n'
            "    tar -xzf tool-v1.2.3.tar.gz -C /usr/local/bin\n"
        )
        offenders = [
            found
            for command in _commands(text)
            if _MOVING_DOWNLOAD.search(command)
            for found in _FORCED_DECOMPRESSOR.findall(command)
        ]
        assert offenders == [], (
            f"the pinned second command must not be indicted by the moving first one, "
            f"got {offenders}"
        )


def test_forced_tar_in_later_run_is_flagged_via_saved_path():
    """A forcing tar in a separate RUN is indicted when its span contains the saved path."""
    text = (
        "RUN wget -O /tmp/ff.tar "
        '"https://download.mozilla.org/?product=firefox-esr-latest-ssl&os=linux64"\n'
        "RUN tar -xjf /tmp/ff.tar -C /opt\n"
    )
    offenders = _offenders(text)
    assert offenders, (
        f"/tmp/ff.tar is extracted with a forced decompressor in a separate RUN "
        f"instruction; the guard must cross RUN boundaries via the saved path; "
        f"got {offenders!r}"
    )


def test_forced_tar_on_different_file_in_same_run_is_not_flagged():
    """A forcing tar on a pinned file in the same RUN as a correct moving extraction is clean.

    Branch 1 binds the verdict to each tar invocation's own token span, not to the
    whole command — so a second tar that extracts a version-pinned archive is not
    indicted merely because a saved path appears elsewhere in the same command.
    """
    text = (
        'RUN wget -O /tmp/ff.tar.xz "https://download.mozilla.org/?product=firefox-esr-latest-ssl" '
        "&& tar -xf /tmp/ff.tar.xz -C /opt "
        "&& tar -xzf tool-v1.2.3.tar.gz -C /usr/local/bin\n"
    )
    offenders = _offenders(text)
    assert offenders == [], (
        f"tar -xzf operates on tool-v1.2.3.tar.gz, not the saved moving-download path; "
        f"branch 1 must bind to the invocation's own span, not the whole command; "
        f"got {offenders!r}"
    )


def test_stdout_sink_download_is_flagged_via_branch2():
    """A piped download that writes no file is indicted via branch 2 (unresolvable).

    Both the glued form (-O-) and the spaced form (-O -) must not be recorded as
    saved paths — '-' is a stdout sink.  With no saved path, branch 2 fires: the
    invocation shares a command with a moving download and the guard cannot resolve
    which file tar reads.
    """
    glued = (
        'RUN wget -O- "https://download.mozilla.org/?product=firefox-esr-latest-ssl" '
        "| tar -xzf -\n"
    )
    assert _offenders(glued), (
        f"wget -O- pipes to stdout; the guard must not record '-' as a saved path "
        f"and must indict the forcing tar via branch 2; got {_offenders(glued)!r}"
    )
    spaced = (
        'RUN wget -O - "https://download.mozilla.org/?product=firefox-esr-latest-ssl" '
        "| tar -xzf -\n"
    )
    assert _offenders(spaced), (
        f"wget -O - (spaced) also pipes to stdout; the guard must not record '-' as "
        f"a saved path and must indict via branch 2; got {_offenders(spaced)!r}"
    )


def test_archive_path_glued_to_option_is_flagged_via_branch1():
    """A saved path glued to a tar option token is matched in the full span, not operands.

    tar --bzip2 --file=/tmp/ff.tar.xz and tar -xjf/tmp/ff.tar.xz both hide the
    archive path inside an option token.  An operand-only scan would miss them;
    span matching catches both.
    """
    base = 'RUN wget -O /tmp/ff.tar.xz "https://download.mozilla.org/?product=firefox-esr-latest-ssl"\n'
    long_form = base + "RUN tar --bzip2 --file=/tmp/ff.tar.xz\n"
    assert _offenders(long_form), (
        f"--file=/tmp/ff.tar.xz glues the saved path to a long option; span matching "
        f"must find it; got {_offenders(long_form)!r}"
    )
    short_glued = base + "RUN tar -xjf/tmp/ff.tar.xz\n"
    assert _offenders(short_glued), (
        f"-xjf/tmp/ff.tar.xz glues the saved path to the short cluster; span matching "
        f"must find it; got {_offenders(short_glued)!r}"
    )


def test_shell_variable_archive_is_flagged_via_branch2():
    """A tar that names its archive through a shell variable is indicted via branch 2.

    The guard cannot resolve the variable, so the span is unresolvable; branch 2
    fires because the invocation shares a command with a moving download.
    """
    text = (
        'RUN wget -O /tmp/ff.tar.xz "https://download.mozilla.org/?product=firefox-esr-latest-ssl" '
        '&& tar -xjf "$FF"\n'
    )
    offenders = _offenders(text)
    assert (
        offenders
    ), f'tar -xjf "$FF" is unresolvable; branch 2 must indict it; got {offenders!r}'


def test_bare_basename_in_later_run_is_flagged_via_branch1():
    """A tar naming the saved file by basename alone (after cd) is flagged via branch 1.

    A basename token with no '/' matches the saved path's basename, provided the
    candidate token itself carries no '/'.
    """
    text = (
        'RUN wget -O /tmp/ff.tar.xz "https://download.mozilla.org/?product=firefox-esr-latest-ssl"\n'
        "RUN cd /tmp && tar -xjf ff.tar.xz\n"
    )
    offenders = _offenders(text)
    assert offenders, (
        f"ff.tar.xz (bare basename) matches the saved /tmp/ff.tar.xz; "
        f"branch 1 must indict it; got {offenders!r}"
    )


def test_basename_collision_with_pinned_path_is_not_flagged():
    """A later RUN whose archive shares a basename but not the full path is clean.

    /tmp/firefox-esr.tar.xz is saved; /opt/vendor/pinned-9.9.9/firefox-esr.tar.xz
    shares its basename but is a different, explicitly version-pinned file.  Because
    the token carries a '/', basename matching is skipped and only full-path matching
    applies — which does not fire.
    """
    text = (
        'RUN wget -O /tmp/firefox-esr.tar.xz "https://download.mozilla.org/?product=firefox-esr-latest-ssl"\n'
        "RUN tar -xJf /opt/vendor/pinned-9.9.9/firefox-esr.tar.xz\n"
    )
    offenders = _offenders(text)
    assert offenders == [], (
        f"/opt/vendor/pinned-9.9.9/firefox-esr.tar.xz differs from the saved path; "
        f"basename matching must not fire on a token containing '/'; got {offenders!r}"
    )


def test_moving_download_without_saved_path_indicts_own_command_only():
    """A wget with no -O/-o falls to branch 2 (no saved path) within its own command.

    The guard must not guess a saved path from the URL — guessing is how false positives
    land on files the download never wrote.  A forcing tar in the same RUN as the
    wget-without-O is indicted conservatively via branch 2.  A forcing tar in a
    *different* RUN is not indicted, because no saved path was recorded to link them
    and they do not share a command with the moving download.
    """
    # A forcing tar in the same command as wget-without-O: indicted via branch 2.
    same_run = (
        'RUN wget "https://download.mozilla.org/?product=firefox-esr-latest-ssl" '
        "&& tar -xjf firefox-esr.tar.xz -C /opt\n"
    )
    assert _offenders(same_run), (
        f"wget with no -O shares a RUN with a forcing tar; branch 2 must indict it "
        f"because no saved path is known; got {_offenders(same_run)!r}"
    )
    # A forcing tar in a separate RUN: no saved path links it, so it must be clean.
    separate_run = (
        'RUN wget "https://download.mozilla.org/?product=firefox-esr-latest-ssl"\n'
        "RUN tar -xjf firefox-esr.tar.xz -C /opt\n"
    )
    assert _offenders(separate_run) == [], (
        f"wget with no -O in a prior RUN records no saved path; the guard must not "
        f"guess one and indict the later tar; got {_offenders(separate_run)!r}"
    )


class TestTheScannerReadsATarInvocationAsTarDoes:
    """Review on 2026-09-19 (Codex, eight P2 findings): the scanner read a tar
    invocation as whitespace-delimited tokens, so an attached option argument, an
    absolute path to the executable, an attached shell operator and tar's
    end-of-options marker each produced a wrong verdict — in both directions.
    """

    _MOVING = '"https://download.mozilla.org/?product=firefox-esr-latest-ssl"'

    def test_an_attached_archive_argument_is_not_read_as_options(self):
        """``-f`` consumes the remainder of its cluster, so the path is not flags."""
        assert _forced_decompressors("tar -xf/tmp/firefox.tar.xz") == [], (
            "the 'z' in the filename is part of -f's argument, not a forcing option; "
            "GNU tar documents -f as --file=ARCHIVE and extracts the attached form"
        )

    def test_a_forcing_option_before_the_attached_argument_is_still_read(self):
        """The characters before ``f`` are still a flag cluster."""
        assert _forced_decompressors("tar -xjf/tmp/ff.tar.xz"), (
            "-xjf/tmp/ff.tar.xz forces bzip2 before -f consumes the path; narrowing "
            "the cluster scan must not drop the forcing option in front of it"
        )

    def test_an_absolute_path_to_tar_is_recognized(self):
        """``/bin/tar`` is a tar invocation; the exact-token check skipped it."""
        text = (
            f"RUN wget -O /tmp/ff.tar {self._MOVING}\n"
            "RUN /bin/tar -xzf /tmp/ff.tar\n"
        )
        assert _offenders(text), (
            "/bin/tar -xzf on the saved moving download must be indicted; matching "
            "the bare token 'tar' also regressed the earlier unanchored regex"
        )

    def test_an_unrelated_executable_ending_in_tar_is_not_a_tar_invocation(self):
        """Matching the basename must not widen to any name ending in ``tar``."""
        text = (
            f"RUN wget -O /tmp/ff.tar {self._MOVING}\n" "RUN mytar -xzf /tmp/ff.tar\n"
        )
        assert (
            _offenders(text) == []
        ), "mytar is not tar; basename matching must compare the whole basename"

    def test_an_attached_shell_operator_bounds_the_invocation(self):
        """Bash runs ``a;b`` without spaces; the scanner must see two invocations.

        The list of forcing options alone cannot tell the two readings apart, so this
        asserts the attribution: the moving download is extracted correctly by the
        first tar, and the forcing option belongs to the second, which reads a pinned
        archive. Scanned as one invocation, the guard hands the second tar's ``-xzf``
        to the first tar's ``/tmp/moving`` and reports a correct template as broken.
        """
        text = (
            f"RUN wget -O /tmp/moving {self._MOVING} && "
            "tar -xf /tmp/moving;tar -xzf pinned-v1.tar.gz\n"
        )
        assert _offenders(text) == [], (
            f"the ';' bounds two tar invocations; -xzf belongs to the one reading "
            f"pinned-v1.tar.gz, not to the one reading the moving download; "
            f"got {_offenders(text)!r}"
        )
        hidden = f"RUN wget -O /tmp/ff.tar {self._MOVING};tar -xzf /tmp/ff.tar\n"
        assert _offenders(hidden), (
            "the ';' glued to both neighbours hid the tar token altogether, so a "
            "forced extraction of the moving download read as clean"
        )

    def test_an_attached_operator_does_not_hide_a_download_or_a_tar(self):
        """``wget …&&tar …`` must not swallow the tokens it is glued to."""
        text = f"RUN wget -O /tmp/ff.tar {self._MOVING}&&tar -xzf /tmp/ff.tar\n"
        assert _offenders(text), (
            "&& glued to its neighbours hid both the URL and the tar token, so a "
            "forced extraction of the moving download read as clean"
        )

    def test_option_scanning_stops_at_the_end_of_options_marker(self):
        """Tokens after ``--`` are operands, not options."""
        assert _forced_decompressors("tar -xf /tmp/moving -- --gzip") == [], (
            "GNU tar extracts a member literally named --gzip here with format "
            "auto-detection; after -- the scanner must stop recognizing options"
        )


class TestTheGuardBindsEachArchiveToItsOwnDownload:
    """The other half of the same review: which file a tar reads, and where it came
    from. Substring containment, whole-span variable detection and whole-command
    download collection each turned a correct template red.
    """

    _MOVING = '"https://download.mozilla.org/?product=firefox-esr-latest-ssl"'
    _PINNED = '"https://example.invalid/tool-v1.2.3.tar.gz"'

    def test_curl_saves_to_an_attached_short_argument(self):
        """``curl -o/tmp/x`` is valid curl and must record the saved path."""
        text = (
            f"RUN curl -o/tmp/ff.tar.xz {self._MOVING}\n"
            "RUN tar -xzf /tmp/ff.tar.xz\n"
        )
        assert _offenders(text), (
            "curl's short options take an attached value exactly as wget's -OFILE "
            "does; an unrecorded saved path leaves the later forced tar undetected"
        )

    @pytest.mark.parametrize(
        "download",
        [
            "curl --output=/tmp/ff.tar.xz",
            "wget --output-document=/tmp/ff.tar.xz",
            "curl --output /tmp/ff.tar.xz",
            "wget --output-document /tmp/ff.tar.xz",
        ],
    )
    def test_a_long_output_option_records_the_saved_path(self, download):
        """The attached long form saves a file exactly as the spaced form does."""
        text = (
            f"RUN {download} {self._MOVING}\n" "RUN tar -xzf /tmp/ff.tar.xz -C /opt\n"
        )
        assert _offenders(text), (
            f"{download!r} saves the moving download to /tmp/ff.tar.xz; losing that "
            f"association leaves the forced extraction in the later RUN undetected, "
            f"because branch 2 only reaches within the download's own command"
        )

    def test_a_saved_path_does_not_match_a_longer_path_that_starts_with_it(self):
        """``/tmp/a`` is not ``/tmp/archive-v1.tar.gz``."""
        text = (
            f"RUN wget -O /tmp/a {self._MOVING}\n"
            "RUN tar -xzf /tmp/archive-v1.tar.gz\n"
        )
        assert _offenders(text) == [], (
            "substring containment made every path with the saved path as a prefix "
            "look like the moving download; the archive reference must match whole"
        )

    def test_a_saved_basename_does_not_match_a_longer_basename(self):
        """``a`` is not ``a.tar.old``."""
        text = (
            f"RUN wget -O /tmp/a {self._MOVING}\n" "RUN cd /tmp && tar -xzf a.tar.old\n"
        )
        assert (
            _offenders(text) == []
        ), "the basename branch had the same containment defect as the path branch"

    def test_each_saved_path_belongs_to_its_own_download(self):
        """One RUN, two downloads: only the moving one contributes a saved path."""
        text = (
            f"RUN wget -O /tmp/moving {self._MOVING} && "
            f"wget -O /tmp/pinned {self._PINNED} && tar -xzf /tmp/pinned\n"
        )
        assert _offenders(text) == [], (
            "/tmp/pinned came from a version-pinned URL, so forcing its format is "
            "correct; collecting every destination in a command that happens to "
            "contain a moving URL condemns it"
        )

    def test_a_moving_download_in_the_same_run_still_records_its_path(self):
        """The pairing must not lose the moving download's own destination."""
        text = (
            f"RUN wget -O /tmp/moving {self._MOVING} && "
            f"wget -O /tmp/pinned {self._PINNED} && tar -xzf /tmp/moving\n"
        )
        assert _offenders(text), (
            "the moving download's destination must still be indicted when a "
            "pinned download shares the RUN with it"
        )

    def test_a_variable_outside_the_archive_reference_is_not_unresolvable(self):
        """``-C "$DEST"`` says nothing about which archive tar reads."""
        text = (
            f"RUN wget -O /tmp/ff.tar.xz {self._MOVING} && "
            "tar -xf /tmp/ff.tar.xz -C /opt && "
            'tar -xzf pinned-v1.tar.gz -C "$DEST"\n'
        )
        assert _offenders(text) == [], (
            "the second tar reads a pinned literal archive; a variable extraction "
            "directory must not make it unresolvable"
        )

    def test_a_variable_archive_reference_is_still_unresolvable(self):
        """Narrowing the check must keep the case it exists for."""
        text = f"RUN wget -O /tmp/ff.tar.xz {self._MOVING} && " 'tar -xjf "$FF"\n'
        assert _offenders(text), (
            'tar -xjf "$FF" names its archive through a variable; branch 2 must '
            "still indict it"
        )


class TestTheScannerReadsTheCommandAsTheShellDoes:
    """Review round 3 on 2026-09-20 (Codex, seven P2 findings): the parser still
    read the command as whitespace-delimited tokens where the shell and the tools
    read it otherwise — a long option's separate argument, an output option inside
    a short-option cluster, an attached redirection, a quoted path with a space, and
    a ``tar`` that is only an argument to another command.
    """

    _MOVING = '"https://download.mozilla.org/?product=firefox-esr-latest-ssl"'
    _PINNED = '"https://example.invalid/tool-v1.2.3.tar.gz"'

    def test_a_long_option_argument_is_not_read_as_an_option(self):
        """``--exclude --gzip`` excludes a pattern named ``--gzip``; nothing is forced."""
        assert _forced_decompressors("tar --exclude --gzip -xf /tmp/moving") == [], (
            "GNU tar declares --exclude=PATTERN, so the next token is its argument; "
            "scanning that argument as an option reports a correct template as broken"
        )
        # Once the argument is consumed, a forcing option AFTER it is still read.
        assert _forced_decompressors("tar --exclude pattern --gzip -xf /tmp/f") == [
            "--gzip"
        ], "consuming the argument must stop at one token"
        # An option whose argument is OPTIONAL takes a value only when attached, so
        # the token after it is another option, exactly as getopt_long reads it.
        assert _forced_decompressors("tar --occurrence --gzip -xf /tmp/f") == [
            "--gzip"
        ], "--occurrence[=N] must not swallow the --gzip that follows it"
        # The archive is still found after a consumed long argument.
        assert _parse_tar_span(["--directory", "/opt", "-xzf", "/tmp/moving"]) == (
            ["-xzf"],
            "/tmp/moving",
        )

    @pytest.mark.parametrize(
        "download",
        [
            "curl -sLo/tmp/ff.tar.xz",
            "curl -sLo /tmp/ff.tar.xz",
            "wget -qO/tmp/ff.tar.xz",
            "wget -qO /tmp/ff.tar.xz",
        ],
    )
    def test_an_output_option_inside_a_short_cluster_records_the_saved_path(
        self, download
    ):
        """``-sLo`` is ``-s -L -o``; the value-less flags in front hide nothing."""
        text = (
            f"RUN {download} {self._MOVING}\n" "RUN tar -xzf /tmp/ff.tar.xz -C /opt\n"
        )
        assert _offenders(text), (
            f"{download!r} saves the moving download to /tmp/ff.tar.xz; curl and "
            f"wget both let value-less short options precede the output option in "
            f"one cluster, and losing the path leaves the later forced tar undetected"
        )

    def test_a_short_option_argument_is_not_read_as_an_output_option(self):
        """``-do=1`` posts the data ``o=1``; ``-d`` consumes the rest of its cluster."""
        recorded = [
            path
            for paths, _ in _download_invocations(f"curl -do=1 {self._MOVING}")
            for path in paths
        ]
        assert recorded == [], (
            f"curl's -d takes an argument, so the 'o' after it is data, not the "
            f"output option; got {recorded!r}"
        )
        # The stdout sink is still recognised when it is glued inside the cluster.
        text = f"RUN wget -qO- {self._MOVING} | tar -xzf -\n"
        assert _offenders(text), (
            "wget -qO- pipes to stdout; the guard must not record '-' as a saved "
            "path and must indict the forcing tar via branch 2"
        )

    @pytest.mark.parametrize(
        "extraction",
        [
            "tar -xzf /tmp/moving>/dev/null",
            "tar -xzf /tmp/moving>/dev/null 2>&1",
            "tar -xzf /tmp/moving 2>/dev/null",
            "tar -xzf >/dev/null /tmp/moving",
            "tar 2>&1 -xzf /tmp/moving",
            "tar -xzf /tmp/moving &>/dev/null",
        ],
    )
    def test_an_attached_redirection_does_not_hide_the_archive(self, extraction):
        """A redirection belongs to the shell; tar's argv never contains it."""
        text = f"RUN wget -O /tmp/moving {self._MOVING}\n" f"RUN {extraction}\n"
        assert _offenders(text), (
            f"{extraction!r} hands tar the archive /tmp/moving and applies the "
            f"redirection itself, so the saved moving path must still match; left "
            f"joined, the archive reads as '/tmp/moving>/dev/null' and the forced "
            f"decompressor escapes"
        )

    def test_a_quoted_path_with_a_space_is_one_argument(self):
        """The shell passes ``"/tmp/my moving.tar"`` as ONE word."""
        text = (
            f'RUN wget -O "/tmp/my moving.tar" {self._MOVING}\n'
            'RUN tar -xzf "/tmp/my pinned-v1.tar.gz"\n'
        )
        assert _offenders(text) == [], (
            f"both paths truncated to /tmp/my by str.split, so a pinned archive was "
            f"reported as the moving file; got {_offenders(text)!r}"
        )
        for spelling in (
            'tar -xzf "/tmp/my moving.tar"',
            "tar -xzf '/tmp/my moving.tar'",
            "tar -xzf /tmp/my\\ moving.tar",
        ):
            hit = (
                f'RUN wget -O "/tmp/my moving.tar" {self._MOVING}\n' f"RUN {spelling}\n"
            )
            assert _offenders(hit), (
                f"{spelling!r} extracts the saved moving file; every quoting of the "
                f"same word must still match"
            )

    def test_a_quoted_operator_is_not_a_separator(self):
        """A ``;`` inside quotes is data, not a command boundary."""
        text = (
            f"RUN wget -O /tmp/moving {self._MOVING} && "
            "echo 'note: a;tar -xzf /tmp/moving is wrong' > /opt/README\n"
        )
        assert _offenders(text) == [], (
            f"the quoted text is one argument to echo; splitting on the ';' inside "
            f"it invented a tar invocation; got {_offenders(text)!r}"
        )

    def test_an_unterminated_quote_fails_closed(self):
        """A command the guard cannot parse is reported, never silently passed."""
        text = f"RUN wget -O /tmp/moving {self._MOVING}\n" 'RUN tar -xf "/tmp/moving\n'
        found = _offenders(text)
        assert found and "unparseable" in found[0], (
            f"an unterminated quote leaves the guard unable to say which file tar "
            f"reads; silence here is the failure mode this file exists to prevent; "
            f"got {found!r}"
        )

    def test_tar_is_recognised_only_at_a_command_position(self):
        """``echo tar -xzf /tmp/moving`` prints a string; it extracts nothing."""
        base = f"RUN wget -O /tmp/moving {self._MOVING}\n"
        for argument in (
            "RUN echo tar -xzf /tmp/moving\n",
            "RUN printf '%s\\n' tar -xzf /tmp/moving > /opt/HOWTO\n",
        ):
            assert _offenders(base + argument) == [], (
                f"{argument.strip()!r} passes 'tar' as an argument to another "
                f"command; matching the basename in any position reported it; "
                f"got {_offenders(base + argument)!r}"
            )

    @pytest.mark.parametrize(
        "spelling",
        [
            "tar -xzf /tmp/moving",
            "/bin/tar -xzf /tmp/moving",
            "cd /tmp && tar -xzf moving",
            "true || tar -xzf /tmp/moving",
            "(cd /tmp; tar -xzf moving)",
            "sudo tar -xzf /tmp/moving",
            "env TAR_OPTIONS= tar -xzf /tmp/moving",
            "TAR_OPTIONS= tar -xzf /tmp/moving",
            "sh -c 'tar -xzf /tmp/moving'",
            "--mount=type=cache,target=/root/.cache tar -xzf /tmp/moving",
        ],
    )
    def test_every_way_of_running_tar_is_still_a_command_position(self, spelling):
        """Every spelling that RUNS tar is reported: as a forcing option when the
        contract reads it, as ``unparseable … needs review`` when it does not (D31)."""
        text = f"RUN wget -O /tmp/moving {self._MOVING}\n" f"RUN {spelling}\n"
        assert _offenders(text), (
            f"{spelling!r} runs tar on the saved moving download — after a control "
            f"operator, behind an assignment or a wrapper, inside a shell -c string, "
            f"or after the RUN instruction's own flags — and must still be reported"
        )


class TestProvenanceFollowsDockerfileOrder:
    """Review round 3 on 2026-09-20: where a file came from was decided file-wide and
    per invocation, not per write and per transfer. A later write marked an earlier
    extraction, a later pinned write never cleared a moving path, and one curl with
    two transfers marked both destinations from the invocation's text.
    """

    _MOVING = '"https://download.mozilla.org/?product=firefox-esr-latest-ssl"'
    _PINNED = '"https://example.invalid/tool-v1.2.3.tar.gz"'

    def test_a_later_moving_write_does_not_indict_an_earlier_extraction(self):
        """The file tar read in RUN 1 did not yet come from the moving download."""
        text = (
            f"RUN wget -O /tmp/a {self._PINNED} && tar -xzf /tmp/a\n"
            f"RUN wget -O /tmp/a {self._MOVING}\n"
        )
        assert _offenders(text) == [], (
            f"/tmp/a held the pinned download when tar read it; a write in a LATER "
            f"RUN cannot change what was extracted; got {_offenders(text)!r}"
        )
        reordered = (
            f"RUN wget -O /tmp/a {self._MOVING}\n"
            f"RUN wget -O /tmp/b {self._PINNED} && tar -xzf /tmp/a\n"
        )
        assert _offenders(
            reordered
        ), "the same write BEFORE the extraction is the defect and stays reported"

    def test_a_later_pinned_write_clears_the_moving_provenance(self):
        """A path overwritten by a pinned download no longer holds the moving file."""
        text = (
            f"RUN wget -O /tmp/a {self._MOVING}\n"
            f"RUN wget -O /tmp/a {self._PINNED} && tar -xzf /tmp/a\n"
        )
        assert _offenders(text) == [], (
            f"the pinned download replaced /tmp/a before tar read it; a one-way "
            f"moving set never forgets a path; got {_offenders(text)!r}"
        )
        one_run = (
            f"RUN wget -O /tmp/a {self._MOVING} && tar -xf /tmp/a && "
            f"wget -O /tmp/a {self._PINNED} && tar -xzf /tmp/a\n"
        )
        assert (
            _offenders(one_run) == []
        ), f"the same sequence inside one RUN is clean too; got {_offenders(one_run)!r}"

    def test_a_write_after_the_extraction_in_the_same_run_is_not_its_source(self):
        """Within one RUN the shell runs left to right, and so does provenance."""
        text = f"RUN tar -xzf /tmp/a && wget -O /tmp/a {self._MOVING}\n"
        assert _offenders(text) == [], (
            f"tar read /tmp/a before the moving download wrote it; got "
            f"{_offenders(text)!r}"
        )

    def test_each_curl_transfer_pairs_with_its_own_output(self):
        """``curl -o A <url1> -o B <url2>``: the first ``-o`` belongs to the first URL."""
        same_run = (
            f"RUN curl -o /tmp/moving {self._MOVING} -o /tmp/pinned {self._PINNED} "
            "&& tar -xzf /tmp/pinned\n"
        )
        assert _offenders(same_run) == [], (
            f"/tmp/pinned received the version-pinned URL; marking both destinations "
            f"from the invocation's text condemned it; got {_offenders(same_run)!r}"
        )
        later_pinned = (
            f"RUN curl -o /tmp/moving {self._MOVING} -o /tmp/pinned {self._PINNED}\n"
            "RUN tar -xzf /tmp/pinned\n"
        )
        assert _offenders(later_pinned) == [], (
            f"the pinned destination stays pinned in a later RUN too; got "
            f"{_offenders(later_pinned)!r}"
        )
        for extraction in (
            f"RUN curl -o /tmp/moving {self._MOVING} -o /tmp/pinned {self._PINNED}\n"
            "RUN tar -xzf /tmp/moving\n",
            f"RUN curl -o /tmp/pinned {self._PINNED} -o /tmp/moving {self._MOVING} "
            "&& tar -xzf /tmp/moving\n",
        ):
            assert _offenders(extraction), (
                "the destination paired with the moving URL is still reported, "
                "whichever transfer comes first"
            )

    def test_an_unpaired_curl_invocation_is_read_conservatively(self):
        """A bare word that is not a URL breaks the pairing; every destination is moving.

        ``--header`` takes a separate argument the guard does not model, so ``Accept:
        x`` is left as a bare word. Pairing outputs with words positionally would hand
        ``/tmp/moving`` to the header text and read it as pinned — the silent skip
        this file exists to prevent. When the pairing cannot be trusted, the whole
        invocation's moving-ness applies, exactly as before this round.
        """
        text = (
            f'RUN curl --header "Accept: application/octet-stream" '
            f"-o /tmp/moving {self._MOVING}\n"
            "RUN tar -xzf /tmp/moving\n"
        )
        assert _offenders(text), (
            "the header text is not a URL, so the pairing is untrusted and the "
            "moving URL in the invocation marks /tmp/moving moving"
        )

    def test_wget_collects_every_url_into_its_one_output(self):
        """``wget -O FILE url1 url2`` concatenates both into FILE."""
        text = (
            f"RUN wget -O /tmp/all {self._PINNED} {self._MOVING} && tar -xzf /tmp/all\n"
        )
        assert _offenders(text), (
            "wget's -O names ONE file for every URL, so a moving URL anywhere in the "
            "invocation makes that file moving"
        )


_M = (
    "https://download.mozilla.org/?product=firefox-esr-latest-ssl&os=linux64&lang=en-US"
)
_P = "https://example.invalid/pinned-1.0.tar.gz"
_SAVE_MOVING = f'RUN wget -O /tmp/a "{_M}"\n'


def _is_unparseable(entry: str) -> bool:
    return entry.startswith("unparseable command ") and entry.endswith(_NEEDS_REVIEW)


class TestTheGuardReadsOnlyItsContract:
    """Issue #519, operator decision of 2026-10-09 (design D31): narrow and fail closed.

    The forms the module docstring names are read and judged. Every other form is
    reported as ``unparseable … needs review`` and not read further, unless a reviewed
    instruction is listed in ``_REVIEWED_UNPARSEABLE``. Each review round on PR #507 and
    PR #626 found a new shell form the parser read wrongly; these tests pin the contract
    instead of the next form.
    """

    @pytest.mark.parametrize(
        "text, expected",
        [
            # #519 row 1: a URL in a comment is not a word.
            ("RUN tar -xzf pinned-v1.tar.gz # https://download.mozilla.org/\n", []),
            # #519 row 5: a traditional option word.
            (_SAVE_MOVING + "RUN tar xzf /tmp/a\n", ["xzf"]),
            # PR #626: dash options after a traditional word are still read.
            (_SAVE_MOVING + "RUN tar xf /tmp/a -z\n", ["-z"]),
            # PR #626: d (compare) is a read mode.
            (_SAVE_MOVING + "RUN tar dzf /tmp/a\n", ["dzf"]),
            # #519 row 2 and PR #626: write modes are not extractions.
            (_SAVE_MOVING + "RUN tar -czf /tmp/a /opt/data\n", []),
            (_SAVE_MOVING + "RUN tar fcz /tmp/a /opt\n", []),
            (_SAVE_MOVING + "RUN tar --delete -zf /tmp/a member\n", []),
            # #519 row 11: a relative save matches an absolute read by basename.
            (f'RUN cd /tmp && wget -O a "{_M}" && tar -xzf /tmp/a\n', ["-xzf"]),
            # Paths are normalised.
            (_SAVE_MOVING + "RUN tar -xzf /tmp//./a\n", ["-xzf"]),
            # #519 row 13: curl's --referer takes an argument.
            (
                f'RUN curl -o /tmp/a --referer https://example.invalid/page "{_M}"\n'
                "RUN tar -xzf /tmp/a\n",
                ["-xzf"],
            ),
            # #519 control: the last write wins, and /tmp/b was never written.
            (
                f'RUN wget -O /tmp/a {_P} && wget -O /tmp/a "{_M}"\n'
                "RUN true\nRUN tar -xzf /tmp/b\n",
                [],
            ),
            # One wget -O collects every URL; a moving one makes the file moving.
            (f'RUN wget -O /tmp/a {_P} "{_M}" && tar -xzf /tmp/a\n', ["-xzf"]),
            # A pipe from a moving download into a forcing tar.
            (f'RUN wget -O - "{_M}" | tar -xz\n', ["-xz"]),
            # An assignment on its own is not in front of tar.
            (_SAVE_MOVING + "RUN V=1 && tar -xzf /tmp/a\n", ["-xzf"]),
            # PR #626: a quoted or escaped name is still tar once the shell reads it.
            (_SAVE_MOVING + "RUN t\\ar -xzf /tmp/a\n", ["-xzf"]),
            (_SAVE_MOVING + "RUN t'a'r -xzf /tmp/a\n", ["-xzf"]),
            # Package installers and echo only take the program names as data.
            (_SAVE_MOVING + "RUN apt-get install -y wget curl tar\n", []),
            # A RUN that names neither tar, wget nor curl is not read.
            ('RUN if [ -n "$X" ]; then echo hi; fi\n', []),
        ],
    )
    def test_a_form_inside_the_contract_is_read(self, text, expected):
        assert _offenders(text) == expected

    @pytest.mark.parametrize(
        "instruction",
        [
            # #519 rows 6, 7, 8, 9, 10, 12, 14, 15, 16.
            "RUN if test -f /tmp/a; then tar -xzf /tmp/a; fi",
            'RUN ["tar", "-xzf", "/tmp/a"]',
            "RUN tar --gzi -xf /tmp/a",
            "RUN TAR_OPTIONS=-z tar -xf /tmp/a",
            "RUN sh -ec 'tar -xzf /tmp/a'",
            f'RUN wget -O /tmp/b "{_M}" || wget -O /tmp/b {_P}',
            'RUN wget -O /tmp/b "$FIREFOX_URL"',
            "RUN sh -c -- 'tar -xzf /tmp/a'",
            "RUN tar -xzf - </tmp/a",
            # PR #626 review threads.
            "RUN sh -- -c -- 'tar -xzf /tmp/a'",
            "RUN tar -xzf - </tmp/pinned </tmp/a",
            "RUN sh -c 'tar -xzf -' </tmp/a",
            "RUN if test -f /tmp/a; then tar -xzf -; fi </tmp/a",
            "RUN 'then' tar -xzf /tmp/a",
            "RUN TAPE=/tmp/pinned.tar.gz tar -xz </tmp/a",
            "RUN for tar in -xzf /tmp/a; do :; done",
            "RUN f() { tar -xzf /tmp/a; }",
            "RUN bash -c 'coproc tar -xzf /tmp/a; wait'",
            # Other forms outside the contract.
            "RUN sudo tar -xzf /tmp/a",
            "RUN nice -n 10 tar -xzf /tmp/a",
            "RUN echo /tmp/a | xargs tar -xzf",
            "RUN eval 'tar -xzf /tmp/a'",
            "RUN (cd /tmp && tar -xzf a)",
            "RUN { tar -xzf /tmp/a; }",
            "RUN tar -xzf /tmp/a &",
            "RUN $TAR -xzf /tmp/a",
            'RUN tar -xzf "$(echo /tmp/a)"',
            'RUN tar -xzf "$ARCHIVE"',
            "RUN tar -zf /tmp/a",
            "RUN tar -xtzf /tmp/a",
            "RUN tar -xzf /tmp/a -f /tmp/b",
            "RUN tar -xzQf /tmp/a",
            f'RUN curl "{_M}" > /tmp/b',
            f'RUN python3 fetch.py "{_M}"',
            "RUN tar -xzf '/tmp/a",
            # A RUN the guard cannot split into words may name tar inside it.
            "RUN echo 'unterminated",
            "tar -xzf /tmp/a",
            'SHELL ["/bin/bash", "-c"]',
            "ADD https://example.invalid/x.tar.gz /tmp/",
            f'ENV FIREFOX_URL="{_M}"',
            "ENV TAR_OPTIONS=-z",
            "ARG TAPE=/tmp/a",
        ],
    )
    def test_a_form_outside_the_contract_fails_closed(self, instruction):
        found = _offenders(_SAVE_MOVING + instruction + "\n")
        assert len(found) == 1 and _is_unparseable(found[0]), found
        assert repr(instruction) in found[0]

    def test_a_second_from_fails_closed(self):
        """#519 row 4: a multi-stage build is not modelled."""
        text = (
            "FROM debian AS one\n"
            + _SAVE_MOVING
            + "FROM debian AS two\nCOPY pinned.tar.gz /tmp/a\nRUN tar -xf /tmp/a\n"
        )
        found = _offenders(text)
        assert len(found) == 1 and _is_unparseable(found[0]), found
        assert "'FROM debian AS two'" in found[0]

    @pytest.mark.parametrize(
        "opener",
        ["RUN <<EOF", "RUN cat <<EOF > /tmp/notes", "RUN cat <<- EOF", "RUN cat <<''"],
    )
    def test_a_heredoc_stops_the_scan(self, opener):
        """#519 row 3 and PR #626: the guard does not look for the end of a body."""
        text = _SAVE_MOVING + opener + "\ntar -xzf /tmp/a\nEOF\nRUN tar -xzf /tmp/a\n"
        found = _offenders(text)
        assert len(found) == 1 and _is_unparseable(found[0]), found
        assert "nothing after it is read" in found[0]
        # A heredoc cannot be allow-listed: its body would still be unread.
        assert _offenders(text, reviewed={opener}) == found

    def test_a_reviewed_instruction_is_skipped(self):
        instruction = "RUN if test -f /tmp/a; then tar -xf /tmp/a; fi"
        text = _SAVE_MOVING + instruction + "\n"
        assert len(_offenders(text)) == 1
        assert _offenders(text, reviewed={instruction}) == []
        # The match is exact: an edited instruction needs a new review.
        assert len(_offenders(text, reviewed={instruction + " "})) == 1

    def test_traditional_words_take_arguments_in_letter_order(self):
        assert _parse_tar_span(["xzCf", "/opt", "/tmp/a"]) == (["xzCf"], "/tmp/a")
        assert _parse_tar_span(["xzfC", "/tmp/a", "/opt"]) == (["xzfC"], "/tmp/a")
        assert _parse_tar_span(["-xfc"]) == ([], "c")
        assert _parse_tar_span(["-xf", "/tmp/a", "xzf"]) == ([], "/tmp/a")


@pytest.mark.parametrize("template", _templates(), ids=lambda p: p.name)
def test_every_template_is_read_or_reviewed(template):
    """Every service template passes the contract, with its reviewed entries only."""
    content = template.read_text(encoding="utf-8")
    found = _offenders(content, reviewed=_REVIEWED_UNPARSEABLE.get(template.name, ()))
    assert found == [], (
        f"{template.name}: {found}. A forced decompressor on a moving download is the "
        f"defect; an 'unparseable' entry is a form the guard does not read. Rewrite it "
        f"in the forms the module docstring lists, or review it and add the exact "
        f"instruction to _REVIEWED_UNPARSEABLE with a comment saying why it is safe."
    )


def test_every_reviewed_entry_is_live_and_holds_no_moving_url():
    """A stale entry hides nothing today but would hide an edit tomorrow."""
    templates = {path.name: path for path in _templates()}
    for name, entries in _REVIEWED_UNPARSEABLE.items():
        assert name in templates, f"{name} is not a service template"
        unreviewed = _offenders(templates[name].read_text(encoding="utf-8"))
        for entry in entries:
            assert not _MOVING_DOWNLOAD.search(entry), (name, entry)
            assert any(
                found.startswith(f"unparseable command {entry!r}")
                for found in unreviewed
            ), f"stale allow-list entry for {name}: {entry!r}"


class TestTheGuardErrsClosedWhereItCannotSeeTheProgram:
    """Adversarial pass on 2026-09-20 over round 3's own fixes, three findings.

    Each is a way the command position or the transfer pairing could be fooled into
    silence: a wrapper option with a separate argument, a URL-shaped argument to a curl
    option the guard did not model, and a program named through a command substitution.
    Where the guard cannot see the program it errs closed.
    """

    _MOVING = '"https://download.mozilla.org/?product=firefox-esr-latest-ssl"'
    _PINNED = '"https://example.invalid/tool-v1.2.3.tar.gz"'

    @pytest.mark.parametrize(
        "spelling",
        [
            "sudo -u root tar -xzf /tmp/moving",
            "nice -n 10 tar -xzf /tmp/moving",
            "env --chdir /tmp tar -xzf moving",
            "sudo -u root sh -c 'tar -xzf /tmp/moving'",
        ],
    )
    def test_a_wrapper_option_with_a_separate_argument_does_not_hide_tar(
        self, spelling
    ):
        """A command that carries ``tar`` as a word may run it, so it fails closed (D31)."""
        text = f"RUN wget -O /tmp/moving {self._MOVING}\n" f"RUN {spelling}\n"
        assert _offenders(text), (
            f"{spelling!r} runs tar; the guard does not know the wrapper's option "
            f"arity, so it must not take the option's argument for the command and "
            f"stop looking"
        )
        # Without a wrapper the rule stays strict: an argument named tar is data.
        plain = (
            f"RUN wget -O /tmp/moving {self._MOVING}\n"
            "RUN echo root tar -xzf /tmp/moving\n"
        )
        assert _offenders(plain) == []

    def test_a_url_shaped_option_argument_does_not_break_the_pairing(self):
        """``--header <url>`` is the header, not a transfer; curl's arity is modelled."""
        for option in ("--header", "--proxy", "--referer", "--user-agent"):
            text = (
                f"RUN curl {option} https://example.invalid/value "
                f"-o /tmp/moving {self._MOVING}\n"
                "RUN tar -xzf /tmp/moving\n"
            )
            assert _offenders(text), (
                f"{option} takes an argument; pairing its URL-shaped value with -o "
                f"read /tmp/moving as pinned and the forced extraction escaped"
            )
        # ``--url`` names a transfer and pairs like a bare URL.
        text = (
            f"RUN curl -o /tmp/moving --url {self._MOVING} -o /tmp/pinned --url {self._PINNED}\n"
            "RUN tar -xzf /tmp/pinned\n"
        )
        assert _offenders(text) == [], f"--url is a transfer; got {_offenders(text)!r}"
        # A long option the table does not know disables the pairing conservatively.
        text = (
            f"RUN curl --some-future-option value -o /tmp/moving {self._MOVING} "
            f"-o /tmp/pinned {self._PINNED}\n"
            "RUN tar -xzf /tmp/pinned\n"
        )
        assert _offenders(text), (
            "an unknown long option may have taken 'value' or not; the guard cannot "
            "pair and must read the invocation whole"
        )

    @pytest.mark.parametrize(
        "spelling",
        [
            "$(echo tar) -xzf /tmp/moving",
            "`which tar` -xzf /tmp/moving",
            "$TAR -xzf /tmp/moving",
        ],
    )
    def test_a_program_the_guard_cannot_name_is_read_as_a_possible_tar(self, spelling):
        """A substituted or variable command word is unresolvable; err closed."""
        text = f"RUN wget -O /tmp/moving {self._MOVING}\n" f"RUN {spelling}\n"
        assert _offenders(text), (
            f"{spelling!r} may run tar; the guard cannot name the program, so a "
            f"forcing option on the saved moving path must still be reported"
        )
        # An unresolvable command word carrying no forcing option is not invented into one.
        clean = (
            f"RUN wget -O /tmp/moving {self._MOVING}\n"
            "RUN $(which ls) -la /tmp/moving\n"
        )
        assert _offenders(clean) == []
        # A substitution is one word; the archive inside it is unresolvable, as before.
        assert _shell_tokens("tar -xzf $(ls /tmp/*.tar) -C /opt") == [
            "tar",
            "-xzf",
            "$(ls /tmp/*.tar)",
            "-C",
            "/opt",
        ]

# Benchmark Results Comparison

**Configuration:** configs/config.yaml  
**Timestamp:** 2026-07-09 04:35:53.225464+00:00  
**Questions Processed:** 27

## Run provenance

⚠️ Whether the run used the selected configuration was **not recorded**: this artifact predates configuration provenance, so no comparison was made.

⚠️ Corpus stability is **unknown**: it was not observed both before and after the run (` None ` → ` None `).

⏱️ Time to ingest is **not recorded**: this artifact predates the field.

- Code version: *not recorded — this artifact predates version stamping*
- Deploy-time commit: ` b6d9a87cb7420d243ea81901641a5297e6707270 ` — frozen by `archi create`; it identifies the deploy, not the image this run used
- Config version: ` sha256:2e158e4ffbbbd6cd1c5f25ce14746d44246427ec11f0e061c8a2c4f00dceb13e `
- Config basis: reconstructed from the configuration file recorded in this artifact; the configuration the agent read was never captured, so this may not describe the run

Settings that define this arm:

| Setting | Value |
|---|---|
| data\_manager.chunk\_overlap | 0 |
| data\_manager.chunk\_size | 1000 |
| data\_manager.chunking | {"strategy": "sentence"} |
| data\_manager.distance\_metric | cosine |
| data\_manager.embedding\_name | HuggingFaceEmbeddings |
| data\_manager.retrievers | {"bm25\_retriever": {"num\_documents\_to\_retrieve": 5}, "hierarchical\_rerank": {"candidate\_pool\_size": 20, "enabled": true, "num\_documents\_to\_retrieve": 5, "reranker": {"model": "ms-marco-MiniLM-L-12-v2"}}, "hybrid\_retriever": {"bm25\_weight": 0.6, "num\_documents\_to\_retrieve": 5, "semantic\_weight": 0.4}, "semantic\_retriever": {"num\_documents\_to\_retrieve": 5}} |
| data\_manager.stemming | {"enabled": false} |
| services.benchmarking.agent\_class | FASRCDocsAgent |
| services.benchmarking.agent\_md\_file | /root/archi/agents/fasrc-inline-v1.md |
| services.benchmarking.mode\_settings | {"ragas\_settings": {"batch\_size": false, "embedding\_model": "HuggingFace", "enabled\_metrics": \["answer\_relevancy", "faithfulness", "context\_precision", "context\_recall"\], "evaluator\_model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0", "evaluator\_ollama\_url": null, "evaluator\_provider": "huit\_bedrock", "timeout": 180}, "sources\_settings": {"default\_match\_field": "url"}} |
| services.benchmarking.model | palmfuture/Qwen3.6-35B-A3B-GPTQ-Int4 |
| services.benchmarking.modes | \["RAGAS", "SOURCES"\] |
| services.benchmarking.provider | openai |
| services.chat\_app.agent\_class | CMSCompOpsAgent |
| services.chat\_app.default\_model | llama3.2 |
| services.chat\_app.default\_provider | local |
| services.chat\_app.recursion\_limit | 50 |
| services.vectorstore.backend | postgres |
| services.vectorstore.distance\_metric | cosine |

## 🎯 Retrieval Accuracy

- **Fully Correct:** 0/27 (0.0%)
- **Incorrect** (no expected sources retrieved): 27

## 📊 Aggregate RAGAS Metrics

| Metric | Score |
|---|---|
| Answer Relevancy | n/a (unscored) |
| Faithfulness | n/a (unscored) |
| Context Precision | n/a (unscored) |
| Context Recall | n/a (unscored) |

---

## Question 1: question\_1

### ❓ Question

I am getting a bit crazy trying to find the location of our folder in globus from our project. I need to use globus to create an end point to share our data. I can see our folder using OnDemand or WinSCP at this path: '/net/rcstorenfs02/ifs/rc\_labs/\[LAB\]/Lab/Projects/DARPA/Phase0/Data/Co-Aligned HP' I can not find that folder in 'Harvard FAS RC Holyoke' like '/n/holylabs/\[LAB\]/Lab/' since that is not part of the lab space (we pay for project space). Can you gide me where I can find it in globus? This should be a easy ticket, sorry for bother you.

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
Looks like that folder wasn't actually shared anywhere. I've added it to the Harvard FAS RC Boston endpoint (as that is where that data lives). You can find it under [LAB]
```

---

## Question 2: question\_2

### ❓ Question

Is it possible to use EGL for off-screen graphics rendering in the gpu\_h200 partition? When I try, my program returns an error about not having a display.

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
Sadly no. Confusingly GPU's in have two modes, compute mode and graphics mode. Our GPU's are in compute node so they can't actually render graphics... Which is pretty hilarious given the name Graphics Processing Unit.
We do have a V100 GPU set in graphics mode in the remoteviz partition which you are welcome to use. We don't have any of our other GPU in graphics mode due to lack of demand and the fact that it is nontrivial to setup.
```

---

## Question 3: question\_3

### ❓ Question

I’m trying to submit jobs to get notebooks via the UI of FasRC, and for some reason, I’m getting the job but can’t enter the notebook. job&lt;\[URL\]&gt; id for example (13950798)

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
Apologies for the inconvenience. We updated the Jupyter app during our maintenance earlier today. It looks like the cuda/10.0.130-fasrc01 module that you are loading (see screenshot cuda_module.png) in the Jupyter app form is causing the issue. Can you remove that and see if Jupyter starts properly?
```

---

## Question 4: question\_4

### ❓ Question

I had a job, 59923907, in queue for around a day. However, the salloc and even the h200 queues are way faster. Are there any errors in the queue? Thanks!

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
Can you provide a job ID for the interactive job? The only interactive job that I see is in gpu_test, not gpu_h200.
The cluster has been very busy for the past few days and your lab's fairshare is low (0.151609):
$ sshare --account=[LAB] -a
```

---

## Question 5: question\_5

### ❓ Question

I am trying to connect to FASSE from my Mac using VS Code Remote-SSH, but VS Code gets stuck at “Opening Remote...” after I select the SSH host. I can connect through a regular terminal SSH session, but VS Code Remote-SSH does not proceed. In VS Code, the Remote-SSH log only shows lines like: \[\[DATE\_TIME\]\] Log Level: 2 \[\[DATE\_TIME\]\] Picking SSH host \[\[DATE\_TIME\]\] Selected fasselogin.rc.fas.harvard.edu After that, nothing else happens. It does not proceed to the usual SSH/server installation steps. I have already tried: - Reinstalling the VS Code Remote-SSH extension - Removing local Remote-SSH cache - Killing/removing the remote VS Code server with: pkill -u $USER -f vscode rm -rf \~/.vscode-server \~/.vscode-remote - Restarting VS Code - Testing regular SSH from the terminal Could you please advise whether there are any recommended VS Code settings or known issues for connecting to FASSE login nodes?

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
To launch VSCode on FASSE, are you following the instructions here: [URL] [[URL]] to get that going?
You would need to initiate a remote tunnel session for launching VSCode on FASSE. Also, if you are interested in starting the tunnel using your local VSCode interface, then you would need to turn off the FASSE VPN, start the tunnel session, and then turn the FASSE VPN back on.
If that doesn't work, then let me know a good time to meet tomorrow afternoon and we can troubleshoot this issue then.
```

---

## Question 6: question\_6

### ❓ Question

I'm reaching out to you because I am getting basically no gpu allocation, it takes me very very long to even ask for 1 gpu\_requeue or itc\_gpu for 15mins... For days I have been waiting for h200 gpus, but I assume someone is running a big job and am happy to wait on that. However, I can't even run small jobs now, so I would like to know if there is a problem, if I have overused my fair-share or if there is any other reason.

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
The reason for you pending is that your lab's fairshare is low:
[root@holy8a24507 general]# sshare --account=[LAB] -a
```

---

## Question 7: question\_7

### ❓ Question

I'm often getting this error message: srun: fatal: --cpus-per-task, --tres-per-task=cpu:#, and --cpus-per-gpu are mutually exclusive with my SLURM script, although sometimes, the exact same script works fine without errors. It is not clear to me what could be causing the error, and why it only happens sometimes (in particular, I'm only specifying cpus-per-task, but non of the other arguments). This is my complete script: #!/bin/bash #SBATCH --nodes=1 #SBATCH --ntasks-per-node=1 #SBATCH --gpus-per-node=1 #SBATCH --cpus-per-task=24 #SBATCH --mem=360G #SBATCH --array=1-1%1 #SBATCH --time=72\[DATE\_TIME\] #SBATCH --job-name=icot #SBATCH --account=\[LAB\] #SBATCH -o /n/netscratch/\[LAB\]/Lab/\[USER\]/ICOT/outs/icot\_%A/%a\_%A.out #SBATCH -e /n/netscratch/\[LAB\]/Lab/\[USER\]/ICOT/errs/icot\_%A/%a\_%A\_%j.err #SBATCH --partition=seas\_gpu #SBATCH --constraint=h100 #SBATCH --open-mode=truncate #SBATCH --mail-type=ALL # Options: BEGIN, END, FAIL, REQUEUE, ALL #SBATCH --mail-user=\[EMAIL\] EXPERIMENT\_NAME="icot" # NOTE: CHOOSE EXP NAME RIGHT! ----------------------------------------- # Make sure this matches the dirs above, as well as the job name mkdir -p/n/netscratch/\[LAB\]/Lab/\[USER\]/ICOT/outs/${EXPERIMENT\_NAME}\_$SLURM\_ARRAY\_JOB\_ID mkdir -p/n/netscratch/\[LAB\]/Lab/\[USER\]/ICOT/errs/${EXPERIMENT\_NAME}\_$SLURM\_ARRAY\_JOB\_ID # NOTE: Switch back to array ID module loadpython/3.10.13-fasrc01 source /n/netscratch/\[LAB\]/Lab/\[USER\]/ICOT/venv/bin/activate srun python/n/netscratch/\[LAB\]/Lab/\[USER\]/ICOT/ICOT/run\_all\_experiments.py Do you see anything that could be causing this error? Anything that looks wrong/suspicious? (For example, this happened in the job with JOBID 16604596, if you want to have a look at that job's output.)

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
You might try changing from gpus-per-node to gpus-per-task? Though its sort of weird that it works sometimes and not other times. Is it working right now?
```

---

## Question 8: question\_8

### ❓ Question

My user name is \[USERNAME\] on fasrc and I have two lab access: \*\[LAB\]\* and \*\[LAB\]\*. I am trying to use seas\_gpu or gpu\_h200 but I noticed that I can access these compute only through \[LAB\] but not \[LAB\]. I tried to specify --account and that does not work. Do you know how I can use the \[LAB\] account to assess seas gpus? I know \[LAB\] should have access because other users under the lab can use these partitions. Therefore, I suspect the issue is on my end. Thank you!

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
You cannot submit jobs under [LAB] because you have not been added to the [LAB]'s fairshare account. Currently, you can access [LAB] storage, but the fairshare requires additional settings.
We need approval from Prof. [NAME], cc'd in this ticket. Prof. [NAME], do you approve adding [NAME] to the [LAB] fairshare?
```

---

## Question 9: question\_9

### ❓ Question

Yesterday, I submitted jobs. The earlier ones were running correctly and executing as expected; however, all subsequent jobs failed with this error (job id: 1338487): /bin/bash: /n/sw/helmod-rocky8/apps/lmod/lmod/init/bash: Stale file handle environment: line 17: /n/sw/helmod-rocky8/apps/lmod/lmod/libexec/lmod: Stale file handle bash: /n/sw/helmod-rocky8/apps/lmod/lmod/init/bash: Stale file handle ./c8\_air\_shower: /lib64/libstdc++.so.6: version \`GLIBCXX\_3.4.32' not found (required by ./c8\_air\_shower) ./c8\_air\_shower: /lib64/libstdc++.so.6: version \`GLIBCXX\_3.4.30' not found (required by ./c8\_air\_shower) ./c8\_air\_shower: /lib64/libstdc++.so.6: version \`GLIBCXX\_3.4.29' not found (required by ./c8\_air\_shower) ./c8\_air\_shower: /lib64/libstdc++.so.6: version \`CXXABI\_1.3.13' not found (required by ./c8\_air\_shower) ./c8\_air\_shower: /lib64/libstdc++.so.6: version \`GLIBCXX\_3.4.26' not found (required by ./c8\_air\_shower) ./c8\_air\_shower: /lib64/libstdc++.so.6: version \`GLIBCXX\_3.4.32' not found (required by /n/holylfs05/LABS/\[LAB\]/Everyone/\[USERNAME\]/corsika\_package/corsika-install/lib/libCONEXsibyll.so) ./c8\_air\_shower: /lib64/libstdc++.so.6: version \`GLIBCXX\_3.4.32' not found (required by /n/holylfs05/LABS/\[LAB\]/Everyone/\[USERNAME\]/corsika\_package/corsika-install/lib/libfluka.so) Could you help identify the issue? Is it system-related, and should I wait for it to be resolved?

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
We’re currently experiencing an issue affecting mounts to the Holyoke Isilon filesystem (specifically /n/sw) on a number of cluster nodes. As a result, the cluster is in a degraded state.

Jobs running during this time may fail or terminate unexpectedly if they encounter affected nodes. We are actively identifying and remediating impacted nodes, which will involve draining and rebooting them. During this process, parts of the cluster may become temporarily unavailable.

We recommend avoiding starting new jobs for approximately the next hour while remediation is underway.

At this time, there is no indication of underlying issues with the Isilon storage itself beyond the impact on cluster nodes. We will provide updates if that changes. You may look for detailed status information and updates at our status page:

[URL] [[URL]] [[URL] [[URL]]]

Thank you for your patience, and please let us know if you have any questions.
```

---

## Question 10: question\_10

### ❓ Question

Since yesterday when I try to submit jobs, they just PD with the following message "(ReqNodeNotAvail, May be reserved for other job)” do you know why that is?

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
Your job is slated for 20 days. That means that this job would not complete before our scheduled OS upgrades: [URL] [[URL]] The error you are seeing there is an indication that it is intersecting with one of the reservations we have setup for this upgrade.
To run you will either need to ask for less time or wait until after the OS upgrade work is complete.
```

---

## Question 11: question\_11

### ❓ Question

I seem to have access to all the partitions under \[LAB\], but I have a limit of 0 jobs I’m able to request. Could you please help me understand how I can request jobs on these partitions under this account?

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
As a HMS lab Zitnik lab is not allowed to use nonKempner partitions. If you need general compute you will want to talk to HMS RC ([URL] [[URL]]).
```

---

## Question 12: question\_12

### ❓ Question

We're running into issues with submitting SLURM jobs on -p eddy. Submitting a job with -t 0 or that requests more than 20 days of run time gives a "Required node not available" error. In the past, our partition has allowed unlimited-time requests, and that's how we'd like it to work, particularly as we have some very long-running experiments coming up. Could someone please take a look at this?

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
In this case you are hitting the reservations we have in place for the OS Upgrade: [URL] [[URL]] These are being used so we don't have to cancel people's jobs. You will need to either ask for less time than the time between now and the scheduled upgrade, or your jobs will pend until after the upgrade is complete.
```

---

## Question 13: question\_13

### ❓ Question

This is a very dumb question, so please feel free to refer me to some basic step-by-step quide if it exists. I am trying to test a small webpage I created that needs to access some data file for its work. Apparently, when I open it in my usual browser, it is prevented from doing this properly, so I was recommended to use python for serving it. Specifically, to run python -m http.server 8000 and then to open in my browser \[URL\] I do not have python installed on my machine so I tried to use RC machine for this. Can you please tell me how to do it? I think I managed to serve the page by running the first line, but not sure how to view it.

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
You can use Python on the cluster by loading a module, for example, the command:
module load python
will load the latest python module.
To search for available modules, you can use the command:
module spider python.
For more information about modules, see our Module intro [[URL]] documentation.
```

---

## Question 14: question\_14

### ❓ Question

I am wondering whether it is possible to install Quarto&lt;\[URL\]&gt; globally on FASRC? It works with many common notebooks and seems like it may be useful for a number of people. Thanks!

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
Which application are you planning to use? Quarto is installed in the RStudio Server app on Open OnDemand. I was able to follow the penguin example in the Quarto guide [[URL]] (see attached screenshot).
```

---

## Question 15: question\_15

### ❓ Question

I recently got a new computer and am trying to set up Globus Connect Personal again. However when I try to login (including using my own setup key generated from the website), I get the following error: "Error: ('relaytool setup failed', CompletedProcess(args='/Applications/Globus Connect Personal.app/Contents/MacOS/bin/relaytool', returncode=1, stdout=b'', stderr=b'’))” The Globus website suggests generating your own setup key could help, but in my case did not. The website does say ”These sort of errors indicate that the workstation where you are attempting to install the Globus Connect Personal Software cannot connect to the Globus service. You can work around this issue and complete your Endpoint setup process by creating a GCP Setup key &lt;\[URL\]&gt;; however, without the network block which is preventing access to \[URL\] \[\[IP\_ADDRESS\]/29 (IPv4) and \[IP\_ADDRESS\] (IPv6)\] being addressed by your Networking team, you will not be able to initiate transfers without moving your machine to a different network (eg. from a home/other network without the network restriction in place).” I don’t fully understands- but seems It maybe an admin (or location for installing?) issue. How can I get Globus Connect Personal to work for me?

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
My colleagues and I are familiar with this particular error. Can you join Virtual Office Hours [[URL]] tomorrow, [DATE_TIME], so we can help you troubleshoot?
```

---

## Question 16: question\_16

### ❓ Question

I have some questions about temporary file storage at FASRC. I reviewed the pages I listed below, but I want to be sure I have the correct understanding. General temporary files: netscratch & local scratch Questions: 1. For Globus, do you have any scratch space related to that? Or is it really on the user's Lab file area as source? Same for destination, receiving files via Globus – only into Lab area? 2. Regarding the stated policy for netscratch, is the deletion policy enforced strictly, as stated on the FASRC web site?

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
Thanks for reaching out. I opened a ticket for wider visibility of this question for our group. But, to answer your questions:
1. General temporary files: netscratch & local scratch - That's correct. However, local scratch is attached to a job. So it will be available to the user for as long as their job is running on a compute node. The moment the job ends, local scratch goes away. So, if someone intends to use local scratch because it is the most performant for storage, they should consider moving the data out to a different storage before the job is done running, as in make it a part of their job launching script to pull the data out before the job ends. See [URL] [[URL]]
2. No, Globus can access /n/netscratch but will not have visibility into anything else other than the Lab and Users/$USER directories. So, if you were to access /n/netscratch/[LAB] via Globus, then that's possible. But Globus will only show Lab and Users folders inside /n/netscratch/[LAB]. If they have not been created, then /n/netscratch/[LAB] will be empty on Globus. See [URL] [[URL]]
Also, if you're interested in moving data around on the cluster from netscratch, then you can consider other CLI tools for accomplishing that. See [URL] [[URL]]
3. You are right, as stated on [URL] [[URL]], the purge policy is strictly enforced unless and until an exception is raised with us, in which case, one will have to open a ticket with FASRC, explain the reason for raising that exception and the duration for which the exception must be honored/active.
Hope that helped. Feel free to drop in our office hours, if you would like to discuss this further. See [URL] [[URL]]
```

---

## Question 17: question\_17

### ❓ Question

I'm processing 8.7 TB of data to collapse to a smaller sample. Each day's (i have 365) processing needs \~750+ GB of RAM for DuckDB window operations. The fasse\_ultramem node works but is currently drained (and will take 35+ days of compute), and the bigmem nodes don't have enough memory. Is there a way to run this on AWS or GCP through Harvard? Thank you.

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
Have you tried using serial_requeue on FASSE? There are several nodes in that partition that could meet your needs and allow you to scale out. The only danger is that your job might be preempted by higher priority work.
If that won't work we can talk about other options internal to the cluster as I would imagine that would be cheaper for all around.
For the record you would need to talk to HUIT for access to AWS and GCP but then you would also have to make sure you DUA's would cover that and work and any storage that is on FASSE is not generally available to those locations except via Globus.
```

---

## Question 18: question\_18

### ❓ Question

What is the difference in I/O speed between reading files from scratch and reading files from storage? scratch: /n/netscratch/\[LAB\]/Lab/\[USER\] storage: /n/holystore01/LABS/\[LAB\]/Lab/\[USER\] I am running a program on scratch and am deciding whether I need to continue my practice of first copying over the relevant files to scratch before running the job, or if I can just keep them in storage. There are about a dozen files that need to be read in. Most are small, but a few are 0.5-2GB in size. I usually run a job array of \~400 jobs at once, in which each of these jobs has to read in the relevant files once at startup. I sometimes run as many as 5,000 such jobs at once.

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
On a practical level I think they are comparable with netscratch being faster by maybe 10% or so. holystore01 is Lustre and so is known to be fast. netscratch is SSD's mounted via RDMA and thus also fast. By our benchmarks they came out roughly neck and neck, we went with VAST due to the ease of operation as Lustre is a pain in the butt to operate well.
Anyways you should be safe reading from holystore01 and shouldn't need to move the data.
```

---

## Question 19: question\_19

### ❓ Question

I have noticed that there has been a dramatic increase in the amount of time it takes to compile software on RC, particularly in the linking steps. After changing a few lines of code and running \`make \[executable\] -j32\` with GNU Make 4.2.1 in an interactive session on a single node, a compilation that took a few seconds a few days ago is now taking many minutes. This issue does not appear to be specific to a particular node.

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
My guess is that the storage you are using is getting hammered. What storage are you compiling on?
```

---

## Question 20: question\_20

### ❓ Question

Could you give me pricing of computer cluster e.g. price per node.hour for both CPU and GPU nodes? So far I was not able to find those information.

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
Our pricing model doesn't really work that way. The cluster is funded via two streams:
1. Overhead from grants: This funds our base operations and general cluster.
2. Hardware Purchases: People can buy additional hardware to add to the cluster.
We don't lease or sell cycles. We do let labs buy hardware if they need it. That said its a pretty bad time to buy as prices are crazy and highly in flux.
Do you need the price per hour for something? If you need a quote or an estimate I can try to provide one if I know what you are looking for.
```

---

## Question 21: question\_21

### ❓ Question

I am having trouble accessing a gpu\_test node for an interactive session via vscode. This is my config file in \~/.ssh: ''' Host cannon User \[USERNAME\] Hostname \[URL\] ControlMaster auto ControlPath \~/.ssh/%r\@%h:%p Host compute UserKnownHostsFile=/dev/null ForwardAgent yes StrictHostKeyChecking no LogLevel ERROR # substitute your username here User \[USERNAME\] RequestTTY yes # Uncomment the command below to get a GPU node on the gpu\_test partition. Comment out the 2nd ProxyCommand ProxyCommand ssh -q cannon "salloc --immediate=180 --job-name=vscode --partition gpu\_test --gres=gpu:1 --time=0-04:00 --mem=4GB --quiet /bin/bash -c 'echo $SLURM\_JOBID &gt; \~/vscode-job-id; nc \\$SLURM\_NODELIST 22'" # Uncomment the command below to get a non-GPU node on the test partition. Comment out the 1st ProxyCommand # ProxyCommand ssh -q cannon "salloc --immediate=180 --job-name=vscode --partition test --time=0-01:00 --mem=4GB --quiet /bin/bash -c 'echo $SLURM\_JOBID &gt; \~/vscode-job-id; nc \\$SLURM\_NODELIST 22'" ''' This is the error I receive: \[DATE\_TIME\] \[DATE\_TIME\] \[info\] Resolving ssh remote authority 'compute' (Unparsed 'ssh-remote+7b22686f73744e616d65223a22636f6d70757465227d') (attempt #1) \[DATE\_TIME\] \[DATE\_TIME\] \[info\] SSH askpass server listening on /var/folders/9c/x7pb3nqj3kz9wvf4qnhmtq\_r0000gt/T/cursor-ssh-U9VXjp/socket.sock \[DATE\_TIME\] \[DATE\_TIME\] \[info\] Using configured platform linux for remote host compute \[DATE\_TIME\] \[DATE\_TIME\] \[info\] Using askpass script: /Users/\[USERNAME\]/.cursor/extensions/anysphere.remote-ssh-1.0.53/dist/scripts/launchSSHAskpass.sh with javascript file /Users/\[USERNAME\]/.cursor/extensions/anysphere.remote-ssh-1.0.53/dist/scripts/sshAskClient.js. Askpass handle: /var/folders/9c/x7pb3nqj3kz9wvf4qnhmtq\_r0000gt/T/cursor-ssh-U9VXjp/socket.sock \[DATE\_TIME\] \[DATE\_TIME\] \[info\] Launching SSH server via shell with command: cat "/var/folders/9c/x7pb3nqj3kz9wvf4qnhmtq\_r0000gt/T/cursor\_remote\_install\_df76cf19-7137-45ac-a3de-264e089a6bcd.sh" \| ssh -T -D 49672 compute bash --login -c bash \[DATE\_TIME\] \[DATE\_TIME\] \[info\] Establishing SSH connection: cat "/var/folders/9c/x7pb3nqj3kz9wvf4qnhmtq\_r0000gt/T/cursor\_remote\_install\_df76cf19-7137-45ac-a3de-264e089a6bcd.sh" \| ssh -T -D 49672 compute bash --login -c bash \[DATE\_TIME\] \[DATE\_TIME\] \[info\] Started installation script. Waiting for it to finish... \[DATE\_TIME\] \[DATE\_TIME\] \[info\] Waiting for SSH handshake (timeout: 120s). Install timeout: 30s. \[DATE\_TIME\] \[DATE\_TIME\] \[info\] Askpass server received request: POST / \[DATE\_TIME\] \[DATE\_TIME\] \[info\] Askpass server received request body: {"request":"(\[EMAIL\]) Password: "} \[DATE\_TIME\] \[DATE\_TIME\] \[info\] Pausing timeout; waiting for askpass response \[DATE\_TIME\] \[DATE\_TIME\] \[info\] Received SSH askpass request: (\[USERNAME\]) Password: \[DATE\_TIME\] \[DATE\_TIME\] \[info\] Resuming timeout; askpass response received \[DATE\_TIME\] \[DATE\_TIME\] \[error\] Error installing server: SSH connection timed out after 120s without receiving any data from the remote host \[DATE\_TIME\] \[DATE\_TIME\] \[info\] Deleting local script /var/folders/9c/x7pb3nqj3kz9wvf4qnhmtq\_r0000gt/T/cursor\_remote\_install\_df76cf19-7137-45ac-a3de-264e089a6bcd.sh \[DATE\_TIME\] \[DATE\_TIME\] \[error\] Error resolving SSH authority SSH connection timed out after 120s without receiving any data from the remote host

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
Are you still facing this issue? If yes, then can you conduct a test by directly SSH'ing into the cluster via the terminal and let me know the result.
Also, were you on the VPN while connecting to the cluster via VSCode using Remote SSH? Few of our users were facing VPN-related problem today, so it's possible that when you tried it there might have been some connectivity issue due to a network glitch.
```

---

## Question 22: question\_22

### ❓ Question

I just realized that I don’t have a folder in the \[URL\]. Would it be possible for you to create a folder there for me? I am currently planning to increase my use of the RC but I am unsure what the path to the above mentioned folder is if I am logged in through an interactive session. So far, I was running batch jobs only from netscratch and saving the data locally on a hard drive, since it is several terabytes in size, which has not been the most comfortable way to work with the data. Additionally, I was planning to use the VS Code tunneling to access the RC resources through my local VS Code but when I tried to run it in the browser I got the error in the image attached. I also could not find the session in my local VS Code installation ‘cannontunnel’, so I am not sure how else I can access it. I was using the Microsoft login option.

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
I've created a Users for you: [URL]/Users/[USERNAME], however the Users folder is now deprecated. We recommend that you use the Labs folder because you can make your own folder there. It is also easier to share data and allows others to save and clean up your folder if you ever leave the lab. Please see: [URL] [[URL]]
On the system, the path is /n/boslfs02/LABS/[LAB] Engert Lab has about 14 TiB available out of 50 TiB total.
# quota -g [LAB] /n/boslfs02
Disk quotas for grp [LAB] (gid 402114):
Filesystem used quota limit grace files quota limit grace
/n/boslfs02 36.03T 50T 50T - 2736911 45088768 45088768
I'll pass this ticket on to our VSCode specialists to investigate that issue. You should hear from them soon.
```

---

## Question 23: question\_23

### ❓ Question

Hello! I have some sequencing data on Harvard storage that I need to upload to NCBI. I am sure that a lot of people need to do this, but I am having trouble figuring out how to make it work, and I would be grateful for some advice! NCBI recommends using “aspera” to upload, although I am not sure if this is the right choice because it seems designed to be used with web browsers. I see there is no module for aspera, so I think I would have to download it onto my home directory. NCBI also gives instructions for using ftp, but I don’t think I can do that from the cluster login node — when I try the command “ftp” it says command not found. Do you recommend that I try to download and install aspera? How have other Harvard cluster users done these transfers?

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
That's great! We really appreciate the update. Just a note that the login nodes have better bandwidth (i.e., better transfer rates) than compute nodes. So the login nodes are is the best option to transfer data externally!
```

---

## Question 24: question\_24

### ❓ Question

I’m currently working on a paper, which involves analyzing sales data from my research group in \[ADDRESS\]. The data is hosted on a secure server in \[ADDRESS\], which requires a VPN connection through UFRGS’s institutional network. To access it, I need to install and run a VPN client (OpenVPN) on my FASRC remote desktop environment. I’ve already downloaded the .ovpn configuration file, but when I try to install the client using the terminal, I realized I don’t have sudo privileges. I also checked, and the system currently does not have OpenVPN pre-installed (which openvpn returns nothing). Would it be possible to either: \* Temporarily grant me sudo access to install OpenVPN, or \* Ask the FASRC team to install OpenVPN on my remote desktop environment? This would allow me to securely retrieve the data needed for our ongoing research without compromising the system. Thank you so much for your help — I really appreciate it! Let me know how you’d prefer me to proceed.

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
First, I would like to check with you if you are following Harvard [[URL]]'s and also FASRC's data security policies. At FASRC, the you can have DSL1 and 2 on Cannon. And DSL3 on FASSE [[URL]], our secure environment.
If you are in agreement with those, you are likely installing the software in a location where you don't have write access. You should install the software in a location that you have write access to, such as your home directory. Can you explain how you are installing?
If you prefer to chat one-on-one, we have office hours on Wednesdays from [DATE_TIME]. For how to join, see [URL] [[URL]].
```

---

## Question 25: question\_25

### ❓ Question

I am writing to request assistance with an issue where my Jupyter Notebook session terminates immediately after starting. I tried updating my .bashrc file as stated in FAQ, but there is no content related to conda initialization, and the problem persists. Could you please advise on how to resolve this issue? Thank you very much for your help.

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
If you look at the output.log file (/n/home11/cfu/.fasrcood/data/sys/dashboard/batch_connect/sys/Jupyter/output/866f3e99-de4a-42ac-90eb-424de7c95eb9.output.log), there is this error:

ImportError: cannot import name 'run_sync_in_worker_thread' from 'anyio' (/n/home11/cfu/.local/lib/python3.8/site-packages/anyio/__init__.py)
We are currently working on an update that will potentially resolve this issue. I will send an update when you can test it. (Open OnDemand is under maintenance right now, and we cannot test it until maintenance is over).
```

---

## Question 26: question\_26

### ❓ Question

I attended FAS RC office hours today and was just starting a ticket as requested by Manasvita to follow up on our discussion. We were hoping to figure out a way to keep models (llama-3.3-70b, llama-3.2-vision-90b) loaded into VRAM on the FASSE H200s, to reduce time and computational overhead associated with reinstantiating models for each user query. We would just need this for the time when we will be running our study (&lt;= 1 month, approx \[DATE\_TIME\]).

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
Thanks for generating the ticket. I had a chat with my team and the most straightforward way is to just download the models you need on /n/netscratch. You need it only for a month, so you won't have to worry about our purge policy. Netscratch has 50TiB of space ([URL] [[URL]]), so storing few tens of GBs wouldn't be an issue. While this won't solve the problem of models loaded into VRAM, at least this would reduce some latency on the user side.
Another option is to have a cron job running on the cluster via Slurm, called scrontab ([URL] [[URL]]), and use that script to load the models during the time you think it would be needed by your users.
A third option is to submit a 3-day job that would just idle waiting for work, it would have the LLM loaded and be ready to go, but it would tie up the GPU for 3 days. Then when it ended you would launch another one. This way, you would ensure that the LLM is always loaded but at the expense of fairshare. I guess this option could be used with setting up an ollama server.
Let me know how you would like to approach this and we'll go from there.
```

---

## Question 27: question\_27

### ❓ Question

I previously reached out regarding options for transferring data between a local server at the Center for Astrophysics (CfA) and the Harvard cluster, and Globus was recommended as the preferred solution. After further investigation, I’m following up to explore how best to implement this in a more automated workflow. My goal is to: 1. Transfer data automatically from a local CfA server (or directly from an acquisition source) to the Harvard cluster using Globus. 2. Trigger the execution of analysis scripts on the cluster as soon as (or on a scheduled basis) new data arrives. 3. Store the processed results locally on the cluster. 4. Transfer the resulting data products back downstream to our server at the CfA. In short, I’m aiming for a fully automated data pipeline where data ingestion, analysis, and result synchronization occur without manual intervention. Could you please advise on the best way to set this up? Specifically: \* Whether Globus is suitable for this. \* If not, what alternative solutions exist? \* Whether it is possible to automate job submission or triggering on the cluster (e.g., a scheduled or event-driven script, cron job, or perhaps even the deployment and execution of a singularity container from our server local to the CfA). \* Any configuration details or permissions required to enable automated two-way transfers between the CfA server and the cluster, ideally without 2FA as 2FA would prevent automated executions.

### 🤖 archi's Answer

```text

```

### ✅ Expected Answer

```text
Globus should be suitable for this assuming you have the endpoint set up. The workflow would be something like:
1. Push data to globus
2. Have script on cluster check storage on cluster for new thing and process it.
3. Have script on CfA end check globus endpoint for update and pull the data
Step 2 could be done via scrontab.
Note that the cluster is not intended for immediate turn around. We cannot guarantee your jobs will run in any give amount of time as that is dependent on your fairshare. As such any work flow you develop will have to accommodate possibly waiting days or weeks for processing. If you need more timely processing I recommend looking at NERC or some cloud provider.

To your final question, the cluster has full access to the internet so if the server is out there then cluster can pull the data down. The trick is going the opposite way, we have no way of exposing our storage aside from Globus. So what works then is:
Push/Pull from the cluster -> Internet
Push/Pull via Globus endpoint from host mounting endpoint
There is not option for:
Push/Pull from Internet -> cluster
So you have to architect around those limitations.
```

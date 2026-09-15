"""
archi utilities module.

Exports core utility services for PostgreSQL-consolidated architecture.
"""

# Configuration services
from src.utils.config_service import (
    ConfigService,
    ConfigValidationError,
    DynamicConfig,
    StaticConfig,
)

# PostgreSQL connection pooling
from src.utils.connection_pool import (
    ConnectionPool,
    ConnectionPoolError,
    ConnectionTimeoutError,
)

# Conversation tracking
from src.utils.conversation_service import (
    ABComparison,
    ConversationService,
    Message,
)

# Document selection (3-tier system)
from src.utils.document_selection_service import (
    DocumentSelection,
    DocumentSelectionService,
)

# Service factory
from src.utils.postgres_service_factory import (
    PostgresServiceFactory,
    create_services,
)

# User management
from src.utils.user_service import (
    User,
    UserService,
)

__all__ = [
    # Connection pool
    "ConnectionPool",
    "ConnectionPoolError",
    "ConnectionTimeoutError",
    # Config
    "ConfigService",
    "StaticConfig",
    "DynamicConfig",
    "ConfigValidationError",
    # Users
    "UserService",
    "User",
    # Document selection
    "DocumentSelectionService",
    "DocumentSelection",
    # Conversations
    "ConversationService",
    "Message",
    "ABComparison",
    # Factory
    "PostgresServiceFactory",
    "create_services",
]

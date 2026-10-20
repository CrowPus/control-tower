"""SQLAlchemy models. Import the package to register every table on `Base`."""

from .automation import (
    AgentTask,
    AICache,
    AIUsage,
    ScheduledJob,
)
from .base import Base, utcnow
from .browser import BrowserSession, BrowserStep
from .identity import LoginAttempt
from .insights import (
    ErrorGroup,
    InsightAlert,
    KpiDaily,
    RequestMetric,
    TelemetryEvent,
    UptimeCheck,
)
from .mail import EmailDelivery, MailboxCursor
from .nia import MEMORY_KINDS, NiaConversation, NiaMeeting, NiaMemory, NiaMessage, OwnerDirective
from .outside import (
    OutsideAgent,
    OutsideAgentCall,
    OutsideAgentMessage,
    OutsideOAuthClient,
    OutsideOAuthGrant,
)
from .oversight import AgentReview
from .platform import (
    AdminAuditLog,
    AdminSession,
    AdminUser,
    PlatformSetting,
)
from .product import Incident, ProductIdea, QaRun
from .social import (
    NETWORKS,
    MarketingMedia,
    MarketingPlan,
    MarketingVideo,
    SocialAccount,
    SocialPost,
)
from .team import (
    TeamAgentState,
    TeamApproval,
    TeamAttachment,
    TeamConversation,
    TeamEvent,
    TeamMessage,
    TeamPlaybook,
    TeamRun,
)
from .webmail import (
    MailAccess,
    MailAccount,
    MailDraft,
    MailFolder,
    MailMessage,
    MailOutbox,
    MailUpload,
)

__all__ = [
    "OutsideAgent", "OutsideAgentCall", "OutsideAgentMessage", "OutsideOAuthClient", "OutsideOAuthGrant",
    "AgentReview",
    "Incident", "ProductIdea", "QaRun",
    "MailAccess", "MailAccount", "MailDraft", "MailFolder", "MailMessage", "MailOutbox", "MailUpload",
    "MarketingMedia", "MarketingPlan", "MarketingVideo", "SocialAccount", "NETWORKS", "SocialPost",
    "BrowserSession", "BrowserStep", "MEMORY_KINDS", "NiaConversation", "NiaMeeting", "NiaMemory", "NiaMessage", "OwnerDirective",
    "TeamAgentState", "TeamApproval", "TeamAttachment", "TeamConversation", "TeamEvent", "TeamMessage", "TeamPlaybook", "TeamRun",
    "ErrorGroup", "InsightAlert", "KpiDaily", "RequestMetric", "TelemetryEvent", "UptimeCheck",
    "EmailDelivery", "MailboxCursor",
    "AdminAuditLog", "AdminSession", "AdminUser", "PlatformSetting",
    "AgentTask", "AICache", "AIUsage", "ScheduledJob",
    "Base", "LoginAttempt", "utcnow",
]

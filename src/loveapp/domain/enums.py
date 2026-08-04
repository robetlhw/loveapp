from enum import StrEnum


class AdviceScenario(StrEnum):
    PURSUIT = "pursuit"
    CONFLICT = "conflict"
    CHAT_ANALYSIS = "chat_analysis"
    RELATIONSHIP_MAINTENANCE = "relationship_maintenance"
    BOUNDARY = "boundary"
    BREAKUP = "breakup"


class RelationshipStage(StrEnum):
    UNKNOWN = "unknown"
    STRANGER = "stranger"
    ACQUAINTANCE = "acquaintance"
    AMBIGUOUS = "ambiguous"
    DATING = "dating"
    STABLE_RELATIONSHIP = "stable_relationship"
    LONG_DISTANCE = "long_distance"
    BREAKUP = "breakup"


class AdviceGoal(StrEnum):
    INITIATE = "initiate"
    UNDERSTAND = "understand"
    PROGRESS = "progress"
    REPAIR = "repair"
    COMMUNICATE = "communicate"
    SET_BOUNDARY = "set_boundary"
    END_RELATIONSHIP = "end_relationship"


class TaskType(StrEnum):
    GENERAL_CHAT = "general_chat"
    RELATIONSHIP_ADVICE = "relationship_advice"
    DATE_PLANNING = "date_planning"


class DateRequestMode(StrEnum):
    NONE = "none"
    EVALUATE = "evaluate"
    CATEGORY_RECOMMENDATION = "category_recommendation"
    PLACE_SEARCH = "place_search"
    ITINERARY = "itinerary"
    MODIFY = "modify"


class DatePlanMode(StrEnum):
    SINGLE_DAY = "single_day"
    MULTI_DAY = "multi_day"


class BudgetScope(StrEnum):
    TOTAL = "total"
    PER_DAY = "per_day"


class DatePlanningStatus(StrEnum):
    COLLECTING = "collecting"
    PLANNED = "planned"
    PAUSED = "paused"
    COMPLETED = "completed"


class DateTaskIntent(StrEnum):
    NONE = "none"
    NEW_REQUEST = "new_request"
    SUPPLEMENT = "supplement"
    CONTINUE = "continue"
    SWITCH = "switch"
    CANCEL = "cancel"


class DatePlanMutation(StrEnum):
    NONE = "none"
    ADD = "add"
    REPLACE = "replace"
    REMOVE = "remove"
    REORDER = "reorder"
    UPDATE_CONSTRAINT = "update_constraint"
    REPLAN = "replan"


class RouteSource(StrEnum):
    RULES = "rules"
    LLM = "llm"
    HYBRID = "hybrid"


class RiskLevel(StrEnum):
    NORMAL = "normal"
    SENSITIVE = "sensitive"
    HIGH = "high"


class SourceType(StrEnum):
    SYNTHETIC_DRAFT = "synthetic_draft"
    REVIEWED_SYNTHETIC = "reviewed_synthetic"
    PUBLIC_REFERENCE = "public_reference"
    SYSTEM_POLICY = "system_policy"


class PlaceCategory(StrEnum):
    RESTAURANT = "restaurant"
    CAFE = "cafe"
    ATTRACTION = "attraction"
    ENTERTAINMENT = "entertainment"


class TransportMode(StrEnum):
    WALKING = "walking"
    TRANSIT = "transit"
    DRIVING = "driving"
    CYCLING = "cycling"

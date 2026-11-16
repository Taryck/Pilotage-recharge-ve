"""Constants for EV Load Manager."""
DOMAIN = "ev_load_manager"
SUBENTRY_TYPE_CHARGER = "charger"
PHASES = ("l1", "l2", "l3")
CONF_THRESHOLD_PREFIX = "max_"
CONF_CURRENT_PREFIX = "current_"
CONF_CHARGER_NAME = "name"
CONF_SETPOINT = "setpoint"
CONF_CONTROL = "control_entity"
CONF_START = "start_value"
CONF_STOP = "stop_value"
CONF_MIN = "min_current"
CONF_MAX = "max_current"

# Controller tuning (installation options).
CONF_ACK_TIMEOUT = "ack_timeout"
CONF_ACK_RETRIES = "ack_retries"
CONF_CONTROL_INTERVAL = "control_interval"
CONF_SETTLE_DELAY = "settle_delay"
DEFAULT_ACK_TIMEOUT = 6.0
DEFAULT_ACK_RETRIES = 2
DEFAULT_CONTROL_INTERVAL = 1.0
DEFAULT_SETTLE_DELAY = 1.0

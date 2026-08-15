import logging
import sys

# Define a custom log level for successful matching operations (sits between INFO and WARNING)
SUCCESS_LEVEL_NUM = 25
logging.addLevelName(SUCCESS_LEVEL_NUM, "SUCCESS")

def success(self, message, *args, **kws):
    """Custom log level method injected into the native Logger class."""
    if self.isEnabledFor(SUCCESS_LEVEL_NUM):
        self._log(SUCCESS_LEVEL_NUM, message, args, **kws)

# Bind success helper to the base Logger class so any native logger can invoke it
logging.Logger.success = success

# Define a high-precision, clean terminal layout matching your original formatting
log_format = '[%(asctime)s.%(msecs)03d] [%(name)s] [%(levelname)s] %(message)s'
date_format = '%Y-%m-%d %H:%M:%S'

# Configure the global root logging system once
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    datefmt=date_format,
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

def get_logger(name):
    """Helper function to fetch a named native logger instance."""
    return logging.getLogger(name)
import time

# Record the time the gateway starts
GATEWAY_START_TIME = time.time()

def get_uptime() -> float:
    return time.time() - GATEWAY_START_TIME
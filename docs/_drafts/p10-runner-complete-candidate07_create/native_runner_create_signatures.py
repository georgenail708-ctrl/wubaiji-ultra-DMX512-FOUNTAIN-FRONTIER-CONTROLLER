"""New reviewed roles only; empty tables refuse under SOURCE_ONLY_HOLD."""
ROLES = {}
DEFAULTS = {}
CLASSES = {}
LAYOUTS = {}
# ROOT must pin dispatch.ContextVar and its exact get/set/reset descriptors,
# before prepare_compat executes; no cold foreign provider is invoked.
PROVIDERS = ()
# New actual patched Panel.add_train and retained called suppliers. Pin exact
# code/defaults/keywords/closure/globals after review; never learn old UI as new.
CONSUMERS = ()
CONSUMER_OWNER = None

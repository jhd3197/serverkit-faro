"""Config defaults + accessor for the serverkit-faro extension backend.

Same contract as serverkit-analytics' config.py: the manifest's
``config_schema`` documents what an admin can edit, but the platform does not
auto-apply the schema's ``default`` values — the canonical defaults live here
and :func:`get_cfg` merges admin-saved values over them.
"""

SLUG = 'serverkit-faro'

# Keep in sync with plugin.json ``config_schema`` defaults.
DEFAULTS = {
    'public_rate_per_min': 30,
}


def get_cfg():
    """Return the effective config: admin-saved values merged over DEFAULTS.

    Safe outside an app/request context (returns a copy of DEFAULTS) so module
    import and background jobs never explode.
    """
    merged = dict(DEFAULTS)
    try:
        from app.plugins_sdk import config as plugin_config
        saved = plugin_config(SLUG) or {}
    except Exception:  # noqa: BLE001 - no app context / plugin row yet
        saved = {}
    for key in DEFAULTS:
        if key in saved and saved[key] is not None:
            merged[key] = saved[key]
    return merged


def cfg_int(key, minimum=None, maximum=None):
    """Read an integer config value, clamped and default-safe."""
    try:
        val = int(get_cfg().get(key, DEFAULTS.get(key)))
    except (TypeError, ValueError):
        val = int(DEFAULTS.get(key, 0))
    if minimum is not None:
        val = max(minimum, val)
    if maximum is not None:
        val = min(maximum, val)
    return val

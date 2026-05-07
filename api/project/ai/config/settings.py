"""Typed access to AI engine configuration."""


def _str2bool(val):
    return str(val).lower() in ("true", "1", "yes", "on")


class AISettings:
    """Typed access to AI engine configuration loaded from ai.yml."""

    def __init__(self, raw):
        self.default_model = raw.get('default_model', 'llama-3_2-3b')

        safety = raw.get('safety', {})
        self.safety_mode = safety.get('mode', 'single')
        self.router_model = safety.get('router_model', '')

        audit = raw.get('audit', {})
        self.audit_enabled = _str2bool(audit.get('enabled', 'false'))
        self.audit_index = audit.get('index', 'malcolm_ai_audit')

"""Mandatory first-batch profile protection rules."""

from psdcalc.reduction.config.models import MatchConfig, ProtectionRule


def rules_for_profile(profile: str | None) -> tuple[ProtectionRule, ...]:
    """Return hard protection rules that user additions cannot silently remove."""
    if profile not in {"ac_pf_strict", "var_control_strict"}:
        return ()
    rules: list[ProtectionRule] = []
    for component in ("Generator", "Transformer", "Link", "ShuntImpedance", "StorageUnit"):
        rules.append(ProtectionRule(id=f"builtin-{component}-identity",
                                    component=component,
                                    action="preserve_component_identity"))
        rules.append(ProtectionRule(id=f"builtin-{component}-terminal",
                                    component=component,
                                    action="preserve_terminal_bus"))
    rules.append(ProtectionRule(
        id="builtin-slack-pv-singleton", component="Generator",
        match=MatchConfig(field="control_role", op="in", value=["Slack", "PV"]),
        action="preserve_bus_as_singleton",
    ))
    rules.append(ProtectionRule(id="builtin-transformer-cannot-link",
                                component="Transformer", action="cannot_link"))
    return tuple(rules)

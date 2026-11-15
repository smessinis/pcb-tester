"""Plugin slot for firmware-in-the-loop.

v1 does not launch Renode. ``build_repl`` turns the pinmux into a board
description a later plugin can feed to a simulator. Replace ``RenodeCheck.run``
or register another object with the same interface from
``pcbtester.checks.registry``.
"""

from __future__ import annotations

from pcbtester.checks.base import Check
from pcbtester.checks.mcu_pins import analyze_mcus
from pcbtester.context import ProjectContext, RunOptions
from pcbtester.models import CheckResult, Finding


class RenodeCheck(Check):
    id = "renode"
    title = "Firmware-in-the-loop (Renode)"
    description = (
        "Plugin slot. v1 writes a Renode board skeleton from the MCU pinmux "
        "and stops. It does not load firmware, clocks or peripherals. A later "
        "plugin can subclass this check and run Renode against the skeleton."
    )
    default_enabled = False

    def run(self, ctx: ProjectContext, options: RunOptions) -> CheckResult:
        del options
        if ctx.netlist is None:
            message = ctx.netlist_error or "No netlist is available, so no board description was built."
            return CheckResult(self.id, self.title, "error", message)
        repl = build_repl(ctx)
        return CheckResult(
            self.id,
            self.title,
            "skipped",
            "Renode was not executed. A board skeleton is attached for a future plugin.",
            findings=[
                Finding(
                    "info",
                    "renode.skeleton",
                    "Generated a Renode platform skeleton from the schematic pinmux. No firmware was loaded.",
                    details=repl,
                )
            ],
            artifacts={"repl": repl, "plugin": "pcbtester.checks.renode:RenodeCheck"},
        )


def build_repl(ctx: ProjectContext) -> str:
    """Text a Renode plugin can turn into a .repl. Not executed."""
    lines = [
        "// PCB Tester Renode skeleton.",
        "// Not loaded by v1. A plugin may write this to a .repl and start Renode.",
        f"// project: {ctx.name}",
    ]
    if ctx.netlist is None:
        lines.append("// no netlist")
        return "\n".join(lines) + "\n"
    _findings, artifacts = analyze_mcus(ctx.netlist)
    rows = artifacts.get("pinmux") or []
    if not rows:
        lines.append("// No supported MCU pin functions were constrained by net names.")
        lines.append("// mach create \"board\"")
        lines.append("// machine LoadPlatformDescription @platforms/cpus/stm32h743.repl")
        return "\n".join(lines) + "\n"
    mcu_id = rows[0].get("mcu", "STM32")
    lines.append(f"// mcu: {mcu_id}")
    lines.append("mach create \"board\"")
    lines.append("// Replace with a platform file you actually have installed, for example:")
    lines.append("// machine LoadPlatformDescription @platforms/cpus/stm32h743.repl")
    lines.append("")
    lines.append("// Peripheral nets inferred from schematic names")
    for row in rows:
        flag = "ok" if row.get("ok") else "CONFLICT"
        af = row.get("af")
        af_text = "-" if af is None else str(af)
        lines.append(
            f"// {flag:8} {row['ref']} {row['pin_name']:6} pin {row['pin']:>3}  "
            f"net {row['net']}  function {row['function']}  AF{af_text}"
        )
    lines.append("")
    lines.append("// gpioPort hook for a plugin: map these nets onto the Renode GPIO controller")
    return "\n".join(lines) + "\n"

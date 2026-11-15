# PCB Tester

PCB Tester takes a KiCad project and runs a pre-production check before you send the board out. It is aimed at MCU boards such as an STM32H743 flight controller: power tree, strap pins, decoupling, pinmux, and a few analog fragments. It does not pretend to simulate the whole microcontroller.

KiCad 7, 8, 9 and 10 projects are read with KiCad 10's `kicad-cli`. A project saved by a newer KiCad than the installed CLI is reported once, as `Project saved with KiCad X, tester has kicad-cli Y`, instead of a load failure on every check.

## Run it

Docker is the setup that already contains KiCad and ngspice:

```bash
docker compose up --build
```

Then open http://localhost:8080. Upload a `.zip` of the project folder (the `.kicad_pro`, schematic, PCB, and any project libraries). 3D models, images, PDFs, Gerber/drill outputs, and KiCad `*-backups` folders are not extracted.

### Upload limits

Real projects are large because of STEP models and fab outputs. The server copies the zip to disk in 1 MB chunks (it does not hold the archive as one bytes object) and only the files it keeps count toward the unzipped size and the file count.

| Variable | Default | What it limits |
| --- | --- | --- |
| `PCBTESTER_MAX_UPLOAD_MB` | 1024 | Size of the uploaded zip |
| `PCBTESTER_MAX_UNZIPPED_MB` | 8192 | Uncompressed size of the files that are extracted |
| `PCBTESTER_MAX_FILES` | 100000 | How many of those files may be written |

`docker-compose.yml` passes the three variables through, so a `.env` file next to it or a shell prefix (`PCBTESTER_MAX_UPLOAD_MB=2048 docker compose up --build`) changes the running container. Each value has to be an integer of at least 1.

Left on disk: KiCad project, schematic, board, and symbol/footprint libraries. Left out:

- 3D models: `.step`, `.stp`, `.wrl`, `.wrz`, `.igs`, `.iges`, `.stl`, and anything under a `.3dshapes` directory
- images (`.png`, `.jpg`, `.svg`, …) and `.pdf`
- Gerber and Excellon outputs (`.gbr`, `.gtl`, `.gbl`, `.drl`, …), including a whole directory named `gerber`, `gerbers`, `fab`, `fabrication`, `jlcpcb`, `pcbway`, or `nextpcb`
- KiCad backup trees, any directory whose name ends in `-backups`, plus `.history/` and `.DS_Store`

`pcbtester run` on a directory you already have on disk does not apply these limits and does not delete those files. It only reads the project, schematic, and board.

On a machine that already has KiCad 10 and ngspice:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pcbtester serve
```

Check a directory from the shell:

```bash
pcbtester run path/to/project --html report.html --pdf report.pdf
```

`--checks` takes a comma-separated list. The default is every check except the Renode slot. `--stimulus VBAT=16.8` overrides a net voltage for the SPICE runs. `--spice id1,id2` limits which detected fragments are simulated.

Rebuild the shipped STM32 pin database (needs network, only when you want a newer embassy export):

```bash
python3 scripts/build_mcu_db.py
```

## What a run does

```
upload / directory
        │
        ▼
kicad-cli sch erc          kicad-cli pcb drc
kicad-cli sch export netlist --format kicadxml
        │
        ▼
netlist ── connectivity / power tree
        ├── MCU pin database (JSON, one file per package)
        └── ngspice decks for dividers, RC filters, behavioral regulators
```

Each check is a class with `id`, `title`, `description` and `run(ctx, options)` in `pcbtester/checks/`. The runner does not care what a check looks at. Register another one in `pcbtester/checks/registry.py`.

| Check | Module | What it uses |
| --- | --- | --- |
| `erc` | `checks/erc.py` | `kicad-cli sch erc` JSON |
| `drc` | `checks/drc.py` | `kicad-cli pcb drc` JSON |
| `connectivity` | `checks/connectivity.py` | KiCad XML netlist |
| `mcu_pins` | `checks/mcu_pins.py` | Netlist plus `pcbtester/mcu/data/*.json` |
| `spice` | `checks/spice.py` | Netlist fragments, then `ngspice -b` |
| `renode` | `checks/renode.py` | Plugin slot. Off by default. Writes a skeleton, does not launch Renode |

`ProjectContext` is the only thing a check is given: project paths, a work directory, and (when the check needs it) the parsed netlist. Checks do not call each other.

## What each check catches, and what it does not

### Schematic ERC

KiCad's own electrical rules: unconnected pins, two outputs fighting, a power input with no power output, off-grid endpoints. Severity is KiCad's severity.

It does not know that PA9 can be USART1_TX, and it does not count capacitors. A custom symbol with the wrong pin type will sail through for the same reason it sails through in KiCad.

### PCB DRC

Clearances, shorts, courtyards, unconnected copper, against the rules stored in the `.kicad_pro`. Schematic parity is not requested, so a board you have not pushed from the schematic is still checked on its own. A PCB file that is only a header, with no footprints, is skipped with "No board layout yet" instead of a load error.

It does not know current. A 0.15 mm track on VBAT is fine if your design rules say it is.

### Connectivity and power

From the netlist only (no coordinates):

- A power pin, NRST, BOOT0 or VCAP that is the only pin on its net.
- Other single-pin nets are one grouped note. Unused GPIOs are normal and are not individual failures.
- I2C nets (name or pin ends in SDA/SCL). A 1 kΩ–10 kΩ resistor to a positive rail is a pull-up. A series resistor of 100 Ω or less does not hide a pull-up on the other side of it. `SCLK` is not treated as SCL.
- BOOT0 strapped to ground, directly or through a resistor. Tied high is a warning (system bootloader). Floating is a failure.
- NRST wants a capacitor to ground. STM32 has an internal pull-up, so a missing external pull-up is not a failure.
- VCAP must not sit on VDD or GND, and wants a capacitor to ground, about 2.2 µF on H7.
- Each IC's VDD/VDDA pins on a rail are counted against local capacitors (1 nF–1 µF) from that rail to ground. The capacitor can be anywhere on the net. This will not catch a 100 nF placed on the opposite edge of the board.
- Regulators are recognised from the library name (`Regulator`) or a known part string (AP2112, TPS62933, AMS1117, …). VIN/VOUT pin names alone are not enough, because video parts use those names for the signal. Switchers are followed from SW through the inductor.
- A rail with power inputs and no power-output pin, regulator, power flag, battery name or connector is a warning. Ferrites and series resistors up to 22 Ω tie filtered copies (VDDA, an IMU rail) back to the parent, so the warning is reported once on the rail that feeds them.

It does not know enabling pins left low, or a buck whose inductor is not a two-pin `L` reference.

### MCU pinmux

Shipped databases: **STM32H743VITx** (LQFP100) and **STM32H743ZITx** (LQFP144), derived from [embassy-rs/stm32-data](https://github.com/embassy-rs/stm32-data) (MIT OR Apache-2.0). See `pcbtester/mcu/data/NOTICE`. AF0 SWD/JTAG names are filled in where the Cube tables omit the reset function.

`STM32H743VIT6` and the KiCad name `STM32H743VITx` both match. A net whose name is a peripheral function (`USART1_TX`, `UART1_TX`, `SPI1_SCLK`, `I2C1_SCL`, `SWDIO`, …) must land on a pin that offers it. The same function on two pins is a conflict. A net that is not a known function is GPIO and is not a failure.

VDD, VDDA and VREF above 3.6 V fail. A GPIO that is not marked 5 V tolerant fails on a net at or above 4.5 V. STM32H743 I/Os are 5 V tolerant in this database, so a 5 V net on a GPIO is an info, not a failure: it is only legal while VDD is present. Power pins are not 5 V tolerant. VDDA sharing the VDD net is reported; a ferrite or a few ohms between them is a pass.

The check does not read firmware. The net name is the only statement of intent. ADC channel numbers and timer alternate functions are checked only when the net is named like the function (`ADC1_INP16`, `TIM1_CH1`).

To add an MCU, drop another JSON file of the same shape into `pcbtester/mcu/data/` (or extend `scripts/build_mcu_db.py` and re-run it). No check code has the H743 pinout hard-coded.

### SPICE

Detected, and shown in the UI so you can turn each one off:

- **Divider.** Two resistors, shared node, other ends a rail and ground. ngspice `.op`. Compared with the resistor ratio. A net named VBAT with no voltage is stimulated at 12.6 V (3S full) and the report says so.
- **RC.** A series resistor into a capacitor to ground. Pull-ups onto a bulk rail capacitor are not treated as filters. Supply filters (rail to rail, resistor up to a few hundred ohms) are. The input step is the rail voltage, or 3.3 V when the input net has no voltage, and the report says which.
- **Regulator.** Only when the part has a fixed voltage in its name (`AP2112K-3.3`) and an output net. The deck is a behavioral clamp, `min(Vset, Vin − 0.3)`, into the output capacitor and a 100 Ω load. It is not a vendor transistor model. If the input voltage or the set point is unknown, the fragment is listed and not run.

There is no Monte Carlo, no op-amp model library, and no loop-stability run.

### Renode slot

`RenodeCheck` builds a text board description from the pinmux (`build_repl`) and returns `skipped`. Nothing is executed. A later plugin replaces `run` and can shell out to Renode. The class is the extension point; it is registered like every other check and left off by default.

## Tests

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

Unit tests cover the value parsers, the KiCad XML netlist, ERC JSON flattening, the connectivity rules, STM32H743 pin matching, ngspice on the sample divider (skipped when ngspice is absent), and HTML/PDF rendering. They use `tests/fixtures/h743_min.xml`, a small netlist with planted mistakes, not a full board.

## Example: Nano H743 AIO

The end-to-end run used the public project [SREESAIARJUN/nano-h743-aio-flight-controller](https://github.com/SREESAIARJUN/nano-h743-aio-flight-controller) (KiCad 8, STM32H743VITx, IMU, baro, OSD, DroneCAN). It is not vendored here. `scripts/demo.sh` clones it and writes HTML, PDF and JSON.

On KiCad 10.0.8, which also opens this KiCad 8 project, that run reported:

- ERC: 139 warnings under KiCad 10. 114 are footprint-link warnings (the custom footprints are not in the stock library), plus 24 isolated labels and one local label that shares a name with a global label.
- DRC: clean.
- Connectivity: I2C pull-ups present (including the baro side of the 33 Ω series resistors), BOOT0 strapped, NRST and VCAP capacitor present, decoupling counted per rail. **+3V3 feeds the MCU and has no regulator output or power flag on this sheet.**
- MCU: 33 peripheral nets (SPI, USART, TIM, SDMMC, USB) match the LQFP100 database. PA8 sits on `VBUS` and is 5 V tolerant, so it is an info rather than a violation.
- SPICE: three RC supply filters (VDDA, both IMU rails) simulated in ngspice.

`scripts/demo.sh` reproduces that.

The same KiCad 10 CLI also opens a hierarchical KiCad 10 design. On [ukinori/flightcontroller](https://github.com/ukinori/flightcontroller) the STM32H743VITx sits on the `untitled.kicad_sch` sub-sheet (`/MCU/`). The netlist export names pins `PA9_68`; the parser keeps the symbol name `PA9`. USB_DM and USB_DP on that sheet match the LQFP100 database. The sibling `meshtransciever.kicad_pcb` is a 79-byte header with no footprints, and DRC is skipped with "No board layout yet".

## Limitations

- One sheet hierarchy is exported by asking `kicad-cli` for the root schematic. A project whose top sheet is not the sibling of the `.kicad_pro` needs that sheet passed to `pcbtester run`.
- Netlist checks do not see placement, loop area, or track width.
- The pin database is only as complete as the embassy export plus the AF0 debug names added in the build script. TT versus FT is a boolean per pin; the shipped H743 parts mark every GPIO as 5 V tolerant.
- Behavioral regulator decks will not catch a wrong feedback divider on a TPS62933. The part number has no fixed voltage, so that regulator is drawn on the power tree and not simulated.
- KiCad and ngspice have to be on `PATH` for the checks that call them. The parsers and the netlist checks still run without them, which is what the unit tests do.

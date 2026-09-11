"""Rules added after watching subagents fail on a real migration.

A field report from a Python-to-Node port recorded thirteen failures across six
parallel agents. The useful finding was not the failure list — it was that most
of the mitigations already existed in this repository, in a file the agent that
needed them does not load.

`search_similar` before writing a helper is described in `orchestration` as
"the one that pays for itself". `orchestration` is loaded by the coordinator,
who is not the one writing helpers; no charter preloads `codelayer`. The
mutation procedure — invert, run, confirm red, restore — is in `tester`'s
charter only, so a builder writing a test as part of an implementation task got
the belief ("a test never observed failing has not been shown to test
anything") without the action.

The fix in both cases was to move the rule to `engineering-baseline`, which all
22 charters preload. These tests pin it there, and pin the parts of each rule
that the report showed were the parts that actually did the work.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BASELINE = REPO / "skills" / "engineering-baseline" / "SKILL.md"
ORCHESTRATION = REPO / "skills" / "orchestration" / "SKILL.md"
TYPESCRIPT = REPO / "skills" / "typescript-rules" / "SKILL.md"
TESTER = REPO / "agents" / "tester.md"


def _collapsed(path: Path) -> str:
    # Collapsed, because a rule that wraps across a line is the same rule and
    # an assertion that breaks on reflowed prose teaches people to delete it.
    return " ".join(path.read_text(encoding="utf-8").split())


@pytest.fixture(scope="module")
def baseline() -> str:
    return _collapsed(BASELINE)


@pytest.fixture(scope="module")
def orchestration() -> str:
    return _collapsed(ORCHESTRATION)


# --- the duplicate a builder is about to write -----------------------------

def test_the_baseline_says_why_the_search_came_back_empty(baseline: str):
    """The whole rule turns on this: the copy is never named like the
    original, so grepping your own name for it proves nothing. A rule that
    only says "don't duplicate" was in the brief, verbatim, three sentences
    long, and the agent duplicated anyway."""
    assert "the duplicate has a different name" in baseline
    assert "That silence is not evidence." in baseline


def test_the_baseline_names_the_third_exit(baseline: str):
    """"Don't edit another agent's file" and "don't duplicate" leave no legal
    move when the function exists but is not exported. Without a third exit
    the agent duplicates — six non-equivalent copies of one function, in the
    reported case. Naming the exit turned those into one-word changes."""
    assert "pending splice" in baseline
    assert "is not exported" in baseline
    assert "a file you do not own" in baseline


def test_a_copy_may_not_be_filed_as_a_deferral(baseline: str):
    """The reported violation arrived as a `ponytail:` note in the agent's own
    report — the duplication rationalised in the vocabulary of good practice,
    which is harder to catch than an unexplained copy."""
    assert "**A copy is not a deferral.**" in baseline
    assert "If you copied anyway, say you copied." in baseline


# --- tests that pass with the code broken ----------------------------------

def test_the_mutation_procedure_reaches_more_than_the_tester(baseline: str):
    """`tester` has carried the procedure all along. Both tests that lied were
    written by agents that are not `tester`."""
    for step in ("invert its assertion", "confirm red", "restore"):
        assert step in baseline, f"baseline never says {step!r}"
    tester = _collapsed(TESTER)
    assert "invert its assertion" in tester, "the charter lost its copy"


def test_a_green_mutation_is_a_finding(baseline: str):
    """The one that caught the event-loop bug. The agent mutated, saw green,
    and investigated instead of reporting a pass — three tasks had reached
    their first await synchronously, so the queueing branch never ran and the
    mutated line was never executed."""
    assert "**A mutation that stays green is a finding, not a pass.**" in baseline


def test_the_indistinguishable_value_trap_is_named(baseline: str):
    """Sixteen tests passed with the splice undone because the broken default
    and the real loader both returned `{}` without a database. Comparing
    results cannot discriminate when both paths produce the same value."""
    assert "comparing results discriminates nothing" in baseline
    assert "Assert on the **call**" in baseline


def test_no_flag_may_manufacture_a_clean_run(baseline: str):
    """Stated language-agnostically in the baseline, because the flag differs
    per runner and the reasoning does not."""
    assert "Never add a flag to make the suite pass or exit." in baseline


# --- the linter ------------------------------------------------------------

def test_lint_zero_never_widens_the_public_api(baseline: str):
    """Six dead functions were exported to silence an unused-symbol warning,
    which announces them as API — worse than the warning it removed."""
    assert "never bought by widening the public API" in baseline


# --- what the coordinator owes the agent -----------------------------------

def test_the_brief_must_cite_paths_that_resolve(orchestration: str):
    """This one is the cost of the rule directly above it: telling briefs to
    cite `path:line` instead of pasting makes an unresolvable path the new
    failure. A brief pointed into a worktree at a file written in the main
    checkout, and the agent stopped without writing a line."""
    assert "`ls` every path the brief cites" in orchestration
    assert "worktree" in orchestration


def test_report_shaped_work_is_written_as_it_goes(orchestration: str):
    """Two agents died before delivering, and there is no record of what they
    had found. A file on disk survives the agent."""
    assert "write it to disk as it goes" in orchestration


# --- the language-specific halves ------------------------------------------

def test_typescript_rules_name_the_module_load_check():
    """A module whose ESM import fails does not load at all, yet its test
    passed — the test mocked that import, so it never loaded the real module.
    Only an import audit found it."""
    text = _collapsed(TYPESCRIPT)
    assert "Don't take a green test as proof the module loads." in text
    assert "--forceExit" in text


def test_typescript_rules_keep_security_last():
    """House rule: security outranks style, and the reader should reach it
    after the style rules. Pinned here because this change edited the file."""
    headings = [
        line for line in TYPESCRIPT.read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    ]
    assert headings[-1].startswith("## Security"), headings


# --- the gate that never ran ------------------------------------------------

def test_orchestration_checks_the_profile_before_dispatching(orchestration: str):
    """Nothing in this repository routed to `/bootstrap`: zero mentions in
    `orchestration`, in the workflow suggester hook, or in any bundled
    workflow. Without a profile `tests_pass` falls back to `pytest -q` and
    returns `passed=True` / `outcome="unverified"` on a repo whose suite is
    Jest — a gate that reads green and never ran. The reported migration was
    exactly such a repo."""
    assert "`/bootstrap` first" in orchestration
    assert "the difference between a gate and a decoration" in orchestration
    assert "outcome=\"unverified\"" in orchestration


def test_a_cloned_profile_is_not_an_approved_one(orchestration: str):
    """Presence is not approval — the same absent/unreadable distinction the
    rest of vise runs on."""
    assert "was never approved on this machine" in orchestration


# ===========================================================================
# Segundo informe de campo — una app de escritorio Electron con su propia
# API, migrada y restilada: 26 incidentes en 11 subagentes.
#
# Cuatro de las trece causas ya tenían mitigación de la tanda anterior y se
# comportaron como se esperaba: un agente citó "el mismo valor no discrimina
# nada" al arreglar un test vacío, otro descartó una mutación inválida solo,
# un tercero reportó un `pending splice` en vez de exportar un archivo ajeno,
# y los hallazgos escritos a disco sobrevivieron a un agente que murió con la
# sesión. Lo que sigue pinea lo que ESA tanda no cubría.
# ===========================================================================

# --- caso 5.1: un criterio de aceptación verde por no haber mirado ---------

def test_the_baseline_makes_the_runner_prove_it_saw_the_new_code(baseline: str):
    """17 tests fuera de los globs de `include` no corrieron y la suite quedó
    verde; el `tsconfig.json` raíz no incluía el paquete nuevo y `tsc --noEmit`
    pasó sin leerlo. Un test ausente no es un test rojo, y la diferencia no se
    ve en el color — se ve en el conteo."""
    assert "New code is not covered until you have seen the runner count it" in baseline
    assert "Note the test count before and after" in baseline
    assert "root `tsconfig.json` passes `--noEmit` without being read" in baseline


def test_the_orchestrator_owns_the_shared_config_files(orchestration: str):
    """La otra mitad: esos dos archivos no eran de nadie. El agente los
    arregló por iniciativa propia y lo marcó — no se puede contar con eso."""
    assert "Config files that describe the whole repo need an owner" in orchestration
    assert "no agent touches and no gate misses" in orchestration


# --- caso 1.1: la dirección del dinero, invertida en el brief --------------

def test_a_directional_claim_must_cite_the_line_that_establishes_it(orchestration: str):
    """El caso más grave del informe. El documento de diseño decía "agregar uno
    devuelve de más" y el código hace lo contrario; el agente derivó el texto
    de la interfaz fielmente y quedó invertido en ambas direcciones, justo
    antes del botón que mueve plata real. Ningún test podía atraparlo: el
    código estaba bien y lo que mentía era la prosa."""
    assert "Verify every directional claim against the code" in orchestration
    assert "the code is correct and the prose is what lies" in orchestration
    assert "does not produce a wrong agent, it produces a wrong product" in orchestration


# --- casos 2.1/2.2/2.3: el contrato adivinado ------------------------------

def test_a_contract_is_quoted_not_paraphrased(orchestration: str):
    """Tres veces el mismo error: el brief describía de memoria lo que otro
    agente estaba construyendo. un campo recordado con un nombre más corto
    obligatorio que devuelve 400 y no estaba mencionado, y la forma de un tipo
    que resultó ser otra máquina de estados."""
    assert "A contract between two agents is quoted, never paraphrased" in orchestration
    assert "path:line" in orchestration
    assert "two agents building faithfully against two different texts" in orchestration


# --- casos 4.1/4.2: partición por archivo contra acoplamiento de tipos -----

def test_the_type_set_is_resolved_as_well_as_the_caller_set(orchestration: str):
    """Agregar dos valores a una unión no cambia ninguna firma, así que el
    pase de llamadores no dispara — y rompió un `Record` exhaustivo en un
    archivo ajeno, y un esquema de validación que devolvía 400 a cada fila con
    un valor que nadie le había contado."""
    assert "the type set" in orchestration
    assert "changes no signature, so the pass above never fires" in orchestration
    assert "400s every row carrying a value nobody told it about" in orchestration


# --- caso 6.2: un comentario sobre un archivo ajeno ------------------------

def test_a_comment_may_not_describe_a_file_you_do_not_own(baseline: str):
    """Era verdad al escribirlo y mentira al aterrizar: el otro agente agregó
    el join que el comentario negaba. En trabajo paralelo eso caduca sin
    que nada avise."""
    assert "Never describe the current state of a file you do not own" in baseline
    assert "lands in someone else's diff" in baseline


# --- caso 10.1: el CLI que instaló paquetes que nadie pidió ----------------

def test_the_baseline_makes_you_diff_the_manifest_after_a_generator(baseline: str):
    """`shadcn` escribió `import { cn } from "cn"` en 16 archivos y agregó el
    paquete `cn` de npm —sin relación con el alias del proyecto— a
    `dependencies`, más `next-themes` y `sonner` en una app que no es Next."""
    assert "diff the manifest and account for every dependency it added" in baseline
    assert "typosquat of the alias you meant" in baseline
    assert "CWE-1357" in baseline


# --- caso 11.1: un CLI interactivo sin TTY --------------------------------

def test_the_baseline_names_the_prompt_that_hangs(baseline: str):
    """`drizzle-kit generate` pregunta si una columna es nueva o un renombre.
    Sin TTY espera para siempre, y `printf '\\n\\n' |` no sirve porque el prompt
    lee del terminal, no de stdin."""
    assert "Running a command that can ask you a question" in baseline
    assert "a prompt reads the terminal, not stdin" in baseline
    assert "drive it with `expect`" in baseline


def test_a_credential_prompt_is_a_stop_not_an_automation_target(baseline: str):
    """El corolario que evita que la regla anterior enseñe lo contrario."""
    assert "is a stop, not a puzzle to automate" in baseline


# --- caso 12.2: una mutación inválida no prueba nada -----------------------

def test_a_suite_that_did_not_run_is_neither_passing_nor_failing(baseline: str):
    """La mutación dejó un error de sintaxis y vitest reportó `no tests`. El
    agente lo descartó solo —"eso no es evidencia de nada"— pero la regla no
    estaba escrita; confundirlas convierte la mutación en teatro."""
    assert "A suite that did not run is neither passing nor failing" in baseline
    assert "Read the count, not the colour." in baseline

from __future__ import annotations

from dataclasses import dataclass

LAUNCH_BANNER = (
"╭──────────────────────────────────────────────────────────────────────────────╮\n"
"│                                                                              │\n"
"│    __     ___ _ _             _    ____          _                           │\n"
"│    \ \   / (_) | | __ _ _ __ (_)  / ___|___   __| | ___                      │\n"
"│     \ \ / /| | | |/ _` | '_ \| | | |   / _ \ / _` |/ _ \                     │\n"
"│      \ V / | | | | (_| | | | | | | |__| (_) | (_| |  __/                     │\n"
"│       \_/  |_|_|_|\__,_|_| |_|_|  \____\___/ \__,_|\___|                     │\n"
"│                                                                              │\n"
"│                      the terminal is under new management                    │\n"
"│                                                                              │\n"
"╰──────────────────────────────────────────────────────────────────────────────╯\n"
)


@dataclass(frozen=True)
class SpinnerTheme:
    frames: list[str]
    slogans: list[str]
    micros: list[str]


def spinner_themes() -> list[SpinnerTheme]:
    slogans = [
        "Villanifying the repo",
        "Villanification underway",
        "Villanivision scanning files",
        "Villaniscope focusing",
        "Villanidering through code",
        "Villaniguard checking edges",
        "Villanigniting the test suite",
        "Villanity check in progress",
        "Villanizer loading context",
        "Villaniation station running",
        "Villaniating patches",
        "Villanilogic compiling thoughts",
        "Villanivating a cleaner UX",
        "Villanimizing display jitter",
        "Villanifying status signals",
        "Villanispecting tool plans",
        "Villani chaos, deployed",
        "Adding Villani sauce",
        "Villani makeover time",
        "Cranking the Villani meter",
        "Summoning Villani energy",
        "Villani polish applied",
        "Letting Villani drive",
        "Villani scrolls consulted",
        "Villani vs spaghetti code",
        "Channeling Villani for CI",
        "Villani vision on",
        "Villani exorcism time",
        "Villani dust deployed",
        "Awaiting Villani guidance",
        "Villani patch incoming",
        "Villani certified vibes",
        "Paging Villani for merge",
        "Villani audit running",
        "Villani folk tale forming",
        "Villani mode enabled",
        "Villani duct tape fix",
        "Bribing with Villani praise",
        "Villani ritual engaged",
        "Villani car wash pass",
        "Villani refactor instincts",
        "Feeding Villani to tests",
        "Villani UX witchcraft",
        "Villani plan brewing",
        "Villani graph diplomacy",
        "Villani confetti warnings",
        "Found Villani, not sanity",
        "Villani lasso ready",
        "Villani logic engaged",
        "Villani resilient upgrade",
        "Villani flashlight logs",
        "Villani discipline time",
        "Villani time travel",
        "Villani TODO prophecy",
        "Villani order restored",
        "Villani optimism only",
    ]
    micros = [
        "Villani-sniffing diffs",
        "Villani-wrangling imports",
        "Villani-juggling tokens",
        "Villani-tuning prompts",
        "Villani-peeking at README",
        "Villani-mapping file paths",
        "Villani-polishing terminal vibes",
    ]
    return [
        SpinnerTheme(["-", "\\", "|", "/"], slogans, micros),
        SpinnerTheme([".", "o", "O", "o"], slogans, micros),
        SpinnerTheme([">  ", ">> ", " >>", "  >", " < ", "<< ", " <<", "  <"], slogans, micros),
        SpinnerTheme(["[   ]", "[=  ]", "[== ]", "[===]", "[ ==]", "[  =]"], slogans, micros),
        SpinnerTheme(["⟦   ⟧", "⟦v  ⟧", "⟦vv ⟧", "⟦vvv⟧", "⟦ vv⟧", "⟦  v⟧"], slogans, micros),
        SpinnerTheme(["⠁", "⠃", "⠇", "⠧", "⠷", "⠿", "⠾", "⠶", "⠦", "⠆", "⠂"], slogans, micros),
        SpinnerTheme(["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"], slogans, micros),
        SpinnerTheme(["<      V>", "<     Vi>", "<    Vil>", "<   Vill>", "<  Villa>", "< Villan>", "<Villani>", "<illani >", "<llani  >", "<lani   >", "<ani    >", "<ni     >", "<i      >", "<       >"], slogans, micros),
    ]

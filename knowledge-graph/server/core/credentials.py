"""Credential detection for text that reaches storage.

Storage is shown to later sessions and is usually a git repository, so a key
that lands there spreads and stays. Two rules, for two kinds of text:

- Memory (gists, notes, touches, edge notes) keeps its case and is written by
  a model, so it is checked against the shapes real credentials have. A write
  that holds one is refused: the model can say where the secret lives instead.
- Recall terms are lowercased words cut from prompts, so known shapes no
  longer match; there a long run that keeps switching between letters and
  digits is enough to drop the term.
"""

import re

# Provider-issued shapes, matched case-sensitively. Checked against every
# stored graph before shipping: no legitimate gist or note matched.
_CREDENTIAL_SHAPES = (
    # Real keys mix digits and capitals; a long kebab id after "-sk-" does not.
    ("Anthropic or OpenAI API key",
     re.compile(r"\bsk-(?:ant-|proj-|svcacct-)?(?=[\w-]*\d)(?=[\w-]*[A-Z])[A-Za-z0-9_-]{32,}")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{50,})")),
    ("GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("Google API key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Stripe secret key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{20,}")),
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{34,}")),
    ("npm token", re.compile(r"\bnpm_[A-Za-z0-9]{36}")),
    ("PyPI token", re.compile(r"\bpypi-[A-Za-z0-9_-]{50,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
)


def credential_kind(text: str) -> str | None:
    """The kind of the first credential found in text, or None."""
    for kind, shape in _CREDENTIAL_SHAPES:
        if shape.search(text):
            return kind
    return None


def refuse_credentials(**fields) -> None:
    """Raise KGError naming the field and kind when any field holds a credential.

    Each value is a string, a list of strings or None. The message never
    repeats the value.
    """
    # Imported here: `kg doctor` loads this file on its own, outside the package.
    from .exceptions import KGError

    for name, value in fields.items():
        for text in [value] if isinstance(value, str) else value or ():
            kind = credential_kind(text)
            if kind:
                raise KGError(
                    f"Not written: what looks like a {kind} is in the {name}. "
                    "Memory is kept in history and shown to later sessions, so it "
                    "never holds a secret's value. Remove the value and send the "
                    "write again; where it helps, say where the secret is kept "
                    "(a file path, a keyring entry, an environment variable's name)."
                )


def term_is_secret_shaped(term: str) -> bool:
    """A long run that keeps switching between letters and digits.

    Keys, tokens and hashes look like this; words, paths and dated filenames
    do not. Such a term never helps retrieval.
    """
    chars = [c for c in term if c.isalnum()]
    if len(chars) < 20:
        return False
    flips = sum(a.isdigit() != b.isdigit() for a, b in zip(chars, chars[1:]))
    return flips >= 4

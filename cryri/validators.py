import logging
from os.path import expanduser, expandvars
from pathlib import Path
from typing import Union, Tuple, Any, List, Dict, Optional


def expand_vars_and_user(
        s: Union[None, str, Tuple[Any], List[Any], Dict[Any, Any]]
) -> Union[None, str, Tuple[Any], List[Any], Dict[Any, Any]]:
    """
    Universal function that returns a copy of an input with values expanded,
    if they are str and contain known expandable parts (`~` home or `$XXX` env var).

    Original object is returned iff it is not a subject for expansion. So, it's better
    read as "this is not an in-place/mutating operation!"
    """

    if s is None:
        return None

    if isinstance(s, tuple):
        # noinspection PyTypeChecker
        return tuple(expand_vars_and_user(x) for x in s)

    if isinstance(s, list):
        return [expand_vars_and_user(x) for x in s]

    if isinstance(s, dict):
        return {k: expand_vars_and_user(v) for k, v in s.items()}

    if not isinstance(s, str):
        return s

    # NB: expand vars then user, since vars could be expanded into a path
    #   that requires user expansion
    # NB: expect only known/existing environment vars to be expanded!
    #   others will be left as-is
    s = expanduser(expandvars(s))

    # notify user if any $'s in the string to catch cases when env vars
    # are expected to be present while they are not (e.g. rc-file sourcing failed)
    if "$" in s:
        logging.warning(
            'After env vars expansion, the value still contains a `$`: "%s".\n'
            'Note: This might be a false alarm — just ensuring a potential silent issue '
            'does not go unnoticed.',
            s
        )

    return s


def sanitize_dir_path(p: Optional[str]) -> Optional[str]:
    if p is None:
        return None

    # NB: it expects already expanded path
    # NB: it expects an existing path (= all parts along the path are existing)

    # resolve path => absolute normalized path
    p = Path(p).resolve()

    # assert path exists, can be accessed and is a directory
    assert p.is_dir(), f'"{p}" is not a directory or path does not exist!'

    return str(p)

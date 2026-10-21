"""Render the configurable max-framing Siril stack command."""

from pathlib import Path

DEFAULT_FEATHER_FLAG = "-feather=100"


def render_max_framing_stack_script(template: str, feather_px: int | None, overlap_norm: bool = True) -> str:
    """Set the feather width while retaining the runnable 100 px SSF default."""
    stack_lines = [line for line in template.splitlines() if line.startswith("stack ")]
    if len(stack_lines) != 1 or "-maximize" not in stack_lines[0]:
        raise ValueError("expected one max-framing stack command")
    if "-norm=addscale" not in stack_lines[0] or "-output_norm" not in stack_lines[0]:
        raise ValueError("overlap normalization requires the existing normalization")
    tokens = stack_lines[0].split()
    if type(overlap_norm) is not bool:
        raise ValueError("overlap_norm must be boolean")
    if tokens.count('-overlap_norm') != 1 or sum(t.startswith('-overlap_norm') for t in tokens) != 1 or sum(t.startswith('-feather') for t in tokens) != 1 or tokens.count(DEFAULT_FEATHER_FLAG) != 1:
        raise ValueError("max-framing stack template lacks unique overlap/feather flags")
    if feather_px is not None and (type(feather_px) is not int or not 1 <= feather_px <= 2000):
        raise ValueError("max-framing feather must be 1..2000 pixels")
    line = stack_lines[0]
    if feather_px is None:
        line = line.replace(' '+DEFAULT_FEATHER_FLAG, '', 1)
    else:
        line = line.replace(DEFAULT_FEATHER_FLAG, f'-feather={feather_px}', 1)
    if not overlap_norm:
        line = line.replace(' -overlap_norm', '', 1)
    return '\n'.join(line if original == stack_lines[0] else original for original in template.split('\n'))


def prepare_stack_script(template_path: Path, work_dir: Path, feather_px: int | None, overlap_norm: bool = True) -> Path:
    """Use a configured work-dir copy for max-framing; leave other scripts alone."""
    template = template_path.read_text(encoding="utf-8")
    if not any("-maximize" in line for line in template.splitlines()
               if line.startswith("stack ")):
        return template_path
    configured_path = work_dir / f"{template_path.stem}_configured.ssf"
    configured_path.write_text(
        render_max_framing_stack_script(template, feather_px, overlap_norm), encoding="utf-8"
    )
    return configured_path


def executed_stack_options(script):
    """Read authoritative flags from the one configured integration command."""
    lines = [line for line in script.splitlines() if line.startswith('stack ')]
    if len(lines) != 1:
        raise ValueError('expected one stack command')
    tokens = lines[0].split()
    overlaps = [t for t in tokens if t.startswith('-overlap_norm')]
    feathers = [t for t in tokens if t.startswith('-feather')]
    if len(overlaps) > 1 or len(feathers) > 1 or any(t != '-overlap_norm' for t in overlaps):
        raise ValueError('ambiguous stack options')
    width = int(feathers[0].split('=',1)[1]) if feathers else 0
    if feathers and not 1 <= width <= 2000:
        raise ValueError('invalid executed feather width')
    return {'command': lines[0], 'overlap_norm': bool(overlaps), 'feather_px': width}


def stack_option_headers(decision, executed):
    """Bind successful-product metadata to verified executed flags, never inference."""
    if decision['applicable']:
        if (executed is None or executed['overlap_norm'] != decision['overlap_norm']
                or executed['feather_px'] != decision['feather_px']):
            raise ValueError('Cannot certify stack options without matching executed command')
    elif executed and (executed['overlap_norm'] or executed['feather_px']):
        raise ValueError('Unexpected max-framing options on non-applicable stack')
    return {'OVERLAP': bool(executed and executed['overlap_norm']),
            'FEATHPX': executed['feather_px'] if executed else 0,
            'OPTAPPL': decision['applicable'],
            'OVRLSRC': decision['options']['overlap_norm']['source'],
            'FTHSRC': decision['options']['feather']['source'],
            'OVRLREQ': str(decision['options']['overlap_norm']['requested']),
            'FTHREQ': str(decision['options']['feather']['requested'])}

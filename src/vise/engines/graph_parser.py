"""Graph Parser - YAML parser for graph.yaml files.

Parses the graph YAML format into Graph, Node, and Edge objects.
Uses a simple hand-rolled parser to avoid PyYAML dependency issues.
"""

import re
from pathlib import Path
from typing import Any

from .graph_engine import Node, Edge, EdgeCondition, Graph, Task


class GraphParseError(Exception):
    """Raised when graph YAML parsing fails."""
    pass


# Agent-runtime enums. Kept here rather than imported from vise.runtime so the
# parser stays importable with no runtime dependency — a workflow file is data,
# and parsing one must not pull in an execution plane.
_VALID_CRITICALITY = frozenset({"routine", "elevated", "critical"})
_VALID_COMPLEXITY = frozenset({"trivial", "low", "medium", "high"})
_VALID_EFFORT = frozenset({"low", "medium", "high", "xhigh", "max"})


def parse_yaml_simple(content: str) -> dict:
    """Simple YAML parser for graph files.

    Handles the specific structure of graph.yaml without external dependencies.
    Supports: nested dicts, lists, strings, ints, bools, multiline strings.
    """
    lines = content.split('\n')

    def get_indent(line: str) -> int:
        """Get indentation level of a line."""
        return len(line) - len(line.lstrip())

    def parse_value(val: str) -> Any:
        """Parse a YAML value string."""
        val = val.strip()
        if not val:
            return None

        # A quoted scalar owns everything up to its CLOSING quote; whatever
        # follows is a trailing comment, and a '#' inside the quotes is
        # content. Finding the close explicitly rather than testing
        # `endswith` is what fixes `- "Bash"  # no shell`: with a comment the
        # value no longer ends in a quote, so the old test failed, the comment
        # strip below ran, and the value came back as '"Bash"' — quotes and
        # all. A tools_blocked entry named '"Bash"' matches no tool, so the
        # gate its author wrote silently blocked nothing.
        if val[0] in '"\'':
            close = val.find(val[0], 1)
            if close != -1:
                return val[1:close]

        # Inline flow sequence: "[]" or "[a, b, \"c\"]". Handles the common
        # `mcps_enabled: []` / `mcps_enabled: [serena, sequentialthinking]`
        # forms that block-list syntax (key on its own line, items indented
        # below) doesn't cover. Same reasoning as the quotes above: take up to
        # the closing bracket so a trailing comment does not turn the list
        # into a string.
        if val.startswith('['):
            close = val.find(']')
            if close != -1:
                inner = val[1:close].strip()
                if not inner:
                    return []
                return [parse_value(item) for item in inner.split(',')]

        # Strip trailing inline YAML comment from unquoted scalar.
        # Block literal content never passes through parse_value, so this is safe.
        # Only strip when '#' is preceded by whitespace: "val  # comment" → "val".
        # Does NOT strip "#fff", "url#anchor", or any '#' with no leading whitespace.
        m = re.search(r'\s#', val)
        if m:
            val = val[:m.start()].strip()

        # Handle booleans
        if val.lower() == 'true':
            return True
        elif val.lower() == 'false':
            return False

        # Handle integers
        if val.lstrip('-').isdigit():
            return int(val)

        # Handle floats (e.g. weight: 1.0) — must come after int check
        try:
            return float(val)
        except ValueError:
            pass

        return val

    def parse_block(start_idx: int, base_indent: int) -> tuple[Any, int]:
        """Parse a block of YAML starting at start_idx.

        Returns (parsed_value, next_line_index)
        """
        if start_idx >= len(lines):
            return None, start_idx

        line = lines[start_idx]
        stripped = line.strip()

        # Skip empty and comment lines
        while start_idx < len(lines) and (not stripped or stripped.startswith('#')):
            start_idx += 1
            if start_idx < len(lines):
                line = lines[start_idx]
                stripped = line.strip()

        if start_idx >= len(lines):
            return None, start_idx

        indent = get_indent(line)

        # Check if this is a list item
        if stripped.startswith('- '):
            # Parse list
            result = []
            while start_idx < len(lines):
                line = lines[start_idx]
                stripped = line.strip()

                if not stripped or stripped.startswith('#'):
                    start_idx += 1
                    continue

                curr_indent = get_indent(line)
                if curr_indent < indent:
                    break
                if curr_indent > indent and result:
                    # Nested content for last item
                    start_idx += 1
                    continue

                if not stripped.startswith('- '):
                    break

                # Parse list item
                item_content = stripped[2:].strip()

                if ':' in item_content:
                    # Dict item like "- id: foo"
                    key, _, val = item_content.partition(':')
                    key = key.strip()
                    val = val.strip()

                    item_dict = {}

                    if val == '|':
                        # Multiline string
                        start_idx += 1
                        ml_lines = []
                        ml_base_indent = indent + 4  # 2 for "- " + 2 for content
                        while start_idx < len(lines):
                            ml_line = lines[start_idx]
                            ml_stripped = ml_line.strip()
                            ml_indent = get_indent(ml_line) if ml_stripped else ml_base_indent

                            if ml_stripped == '' or ml_indent >= ml_base_indent:
                                if len(ml_line) > ml_base_indent:
                                    ml_lines.append(ml_line[ml_base_indent:])
                                else:
                                    ml_lines.append('')
                                start_idx += 1
                            else:
                                break
                        item_dict[key] = '\n'.join(ml_lines).rstrip()
                    elif val:
                        item_dict[key] = parse_value(val)

                    # Parse remaining keys in this dict
                    start_idx += 1
                    while start_idx < len(lines):
                        inner_line = lines[start_idx]
                        inner_stripped = inner_line.strip()

                        if not inner_stripped or inner_stripped.startswith('#'):
                            start_idx += 1
                            continue

                        inner_indent = get_indent(inner_line)

                        # If we hit another list item at same level or lower indent, stop
                        if inner_indent <= indent:
                            break

                        if inner_stripped.startswith('- '):
                            # Nested list - find the key
                            break

                        # Parse key: value
                        if ':' in inner_stripped:
                            ikey, _, ival = inner_stripped.partition(':')
                            ikey = ikey.strip()
                            ival = ival.strip()

                            if ival == '|':
                                # Multiline
                                start_idx += 1
                                ml_lines = []
                                ml_base_indent = inner_indent + 2
                                while start_idx < len(lines):
                                    ml_line = lines[start_idx]
                                    ml_indent = get_indent(ml_line) if ml_line.strip() else ml_base_indent

                                    if ml_line.strip() == '' or ml_indent >= ml_base_indent:
                                        if len(ml_line) > ml_base_indent:
                                            ml_lines.append(ml_line[ml_base_indent:])
                                        else:
                                            ml_lines.append('')
                                        start_idx += 1
                                    else:
                                        break
                                item_dict[ikey] = '\n'.join(ml_lines).rstrip()
                            elif not ival:
                                # Could be nested structure
                                start_idx += 1
                                # Look ahead, past anything that carries no
                                # value. A comment or a blank line between a key
                                # and its block used to fall through both
                                # branches, which dropped the value *and* every
                                # sibling key after it — `security-audit`'s
                                # `fix-criticals` shipped with no validators, no
                                # name and no prompt because of one comment. The
                                # dict branch below already skips these.
                                while start_idx < len(lines):
                                    peek = lines[start_idx].strip()
                                    if peek and not peek.startswith('#'):
                                        break
                                    start_idx += 1
                                if start_idx < len(lines):
                                    next_line = lines[start_idx]
                                    next_stripped = next_line.strip()
                                    if next_stripped.startswith('- '):
                                        # It's a list
                                        nested_list, start_idx = parse_block(start_idx, inner_indent + 2)
                                        item_dict[ikey] = nested_list
                                    elif next_stripped and ':' in next_stripped:
                                        # It's a dict
                                        nested_dict, start_idx = parse_block(start_idx, inner_indent + 2)
                                        item_dict[ikey] = nested_dict
                            else:
                                item_dict[ikey] = parse_value(ival)
                                start_idx += 1
                        else:
                            start_idx += 1

                    result.append(item_dict)
                else:
                    # Simple list item
                    result.append(parse_value(item_content))
                    start_idx += 1

            return result, start_idx

        elif ':' in stripped:
            # Parse dict
            result = {}
            while start_idx < len(lines):
                line = lines[start_idx]
                stripped = line.strip()

                if not stripped or stripped.startswith('#'):
                    start_idx += 1
                    continue

                curr_indent = get_indent(line)
                if curr_indent < indent:
                    break

                if curr_indent > indent:
                    start_idx += 1
                    continue

                if ':' not in stripped:
                    start_idx += 1
                    continue

                key, _, val = stripped.partition(':')
                key = key.strip()
                val = val.strip()

                if val == '|':
                    # Multiline string
                    start_idx += 1
                    ml_lines = []
                    ml_base_indent = indent + 2
                    while start_idx < len(lines):
                        ml_line = lines[start_idx]
                        ml_indent = get_indent(ml_line) if ml_line.strip() else ml_base_indent

                        if ml_line.strip() == '' or ml_indent >= ml_base_indent:
                            if len(ml_line) > ml_base_indent:
                                ml_lines.append(ml_line[ml_base_indent:])
                            else:
                                ml_lines.append('')
                            start_idx += 1
                        else:
                            break
                    result[key] = '\n'.join(ml_lines).rstrip()
                elif val:
                    result[key] = parse_value(val)
                    start_idx += 1
                else:
                    # Empty value - nested structure
                    start_idx += 1
                    if start_idx < len(lines):
                        nested_val, start_idx = parse_block(start_idx, indent + 2)
                        result[key] = nested_val

            return result, start_idx

        return None, start_idx + 1

    result, _ = parse_block(0, -2)
    return result if isinstance(result, dict) else {}


def parse_graph_yaml(content: str) -> Graph:
    """Parse graph YAML content into a Graph object.

    Args:
        content: YAML content as string

    Returns:
        Parsed Graph object

    Raises:
        GraphParseError: If parsing fails or validation errors occur
    """
    try:
        data = parse_yaml_simple(content)
    except Exception as e:
        raise GraphParseError(f"Failed to parse YAML: {e}") from e

    # Extract metadata
    metadata = data.get('metadata', {})
    if not isinstance(metadata, dict):
        metadata = {}

    # Create graph
    graph = Graph(metadata=metadata)

    # Parse nodes
    nodes_data = data.get('nodes', [])
    if not isinstance(nodes_data, list):
        raise GraphParseError("'nodes' must be a list")

    for node_data in nodes_data:
        if not isinstance(node_data, dict):
            continue

        node_id = node_data.get('id')
        if not node_id:
            raise GraphParseError("Node missing required 'id' field")

        # Get mcps_enabled, handling both list and single value
        mcps_enabled = node_data.get('mcps_enabled', ['*'])
        if isinstance(mcps_enabled, str):
            mcps_enabled = [mcps_enabled]
        elif not isinstance(mcps_enabled, list):
            mcps_enabled = ['*']

        # Get tools_blocked
        tools_blocked = node_data.get('tools_blocked', [])
        if isinstance(tools_blocked, str):
            tools_blocked = [tools_blocked]
        elif not isinstance(tools_blocked, list):
            tools_blocked = []

        # Parse contracts (optional) — list of {"file": "...", "content": "..."}
        contracts_raw = node_data.get('contracts')
        contracts: list[dict] | None = None
        if isinstance(contracts_raw, list):
            contracts = []
            for item in contracts_raw:
                if not isinstance(item, dict):
                    continue
                file_val = item.get('file')
                content_val = item.get('content')
                if file_val and content_val is not None:
                    contracts.append({"file": str(file_val), "content": str(content_val)})
            if not contracts:
                contracts = None

        # Parse validators (optional) — list of validator config dicts,
        # same shape as goal validator_configs (e.g. {"type": "tests_pass", ...}).
        validators_raw = node_data.get('validators')
        validators: list[dict] = []
        if isinstance(validators_raw, list):
            for item in validators_raw:
                if isinstance(item, dict):
                    validators.append(item)

        # Parse recipe (optional) — name of a recipe used as a node gate.
        recipe_raw = node_data.get('recipe')
        recipe = str(recipe_raw) if recipe_raw else None

        # Parse node_type (optional, defaults to "wave")
        node_type = str(node_data.get('node_type', 'wave'))
        if node_type not in ('wave', 'dag', 'milestone', 'advisor-gate'):
            raise GraphParseError(f"Node '{node_id}' has invalid node_type: '{node_type}'")

        # Parse tasks (optional, only for dag nodes)
        tasks_raw = node_data.get('tasks', [])
        tasks = []
        if isinstance(tasks_raw, list):
            for task_data in tasks_raw:
                if not isinstance(task_data, dict):
                    continue
                task_id = task_data.get('id')
                if not task_id:
                    raise GraphParseError(f"Task in node '{node_id}' missing 'id'")
                deps = task_data.get('dependencies', [])
                if isinstance(deps, str):
                    deps = [deps]
                t_tools_blocked = task_data.get('tools_blocked', [])
                if isinstance(t_tools_blocked, str):
                    t_tools_blocked = [t_tools_blocked]
                t_mcps = task_data.get('mcps_enabled', ['*'])
                if isinstance(t_mcps, str):
                    t_mcps = [t_mcps]
                t_ownership = task_data.get('ownership', [])
                if isinstance(t_ownership, str):
                    t_ownership = [t_ownership]
                t_acceptance = task_data.get('acceptance', [])
                if isinstance(t_acceptance, str):
                    t_acceptance = [t_acceptance]
                # Fail closed on the runtime enums. A typo'd criticality that
                # silently fell back to 'routine' would route a security-critical
                # task to the cheapest model and look like it worked.
                criticality = str(task_data.get('criticality', 'routine'))
                if criticality not in _VALID_CRITICALITY:
                    raise GraphParseError(
                        f"Node '{node_id}' task '{task_id}' has invalid criticality "
                        f"'{criticality}'; must be one of {sorted(_VALID_CRITICALITY)}"
                    )
                complexity = str(task_data.get('complexity', 'medium'))
                if complexity not in _VALID_COMPLEXITY:
                    raise GraphParseError(
                        f"Node '{node_id}' task '{task_id}' has invalid complexity "
                        f"'{complexity}'; must be one of {sorted(_VALID_COMPLEXITY)}"
                    )
                effort = task_data.get('effort')
                if effort is not None and str(effort) not in _VALID_EFFORT:
                    raise GraphParseError(
                        f"Node '{node_id}' task '{task_id}' has invalid effort "
                        f"'{effort}'; must be one of {sorted(_VALID_EFFORT)}"
                    )
                tasks.append(Task(
                    id=str(task_id),
                    name=str(task_data.get('name', task_id)),
                    prompt=task_data.get('prompt'),
                    dependencies=deps,
                    tools_blocked=t_tools_blocked,
                    mcps_enabled=t_mcps,
                    role=(str(task_data['role']) if task_data.get('role') else None),
                    ownership=[str(o) for o in t_ownership],
                    criticality=criticality,
                    complexity=complexity,
                    writes=bool(task_data.get('writes', True)),
                    model=(str(task_data['model']) if task_data.get('model') else None),
                    effort=(str(effort) if effort else None),
                    acceptance=[str(a) for a in t_acceptance],
                    max_cost=float(task_data.get('max_cost', 0) or 0),
                    max_turns=int(task_data.get('max_turns', 0) or 0),
                    timeout_s=int(task_data.get('timeout_s', 0) or 0),
                    requires_human=bool(task_data.get('requires_human', False)),
                ))

        advisor_reason = node_data.get('advisor_reason') or node_data.get('reason')
        on_decision_raw = node_data.get('on_decision', {})
        on_decision = {
            str(k): str(v)
            for k, v in (on_decision_raw.items() if isinstance(on_decision_raw, dict) else [])
        }
        if node_type == "advisor-gate" and not advisor_reason:
            raise GraphParseError(
                f"Node '{node_id}' is advisor-gate but missing 'advisor_reason'/'reason'"
            )
        node = Node(
            id=node_id,
            name=node_data.get('name', node_id),
            mcps_enabled=mcps_enabled,
            tools_blocked=tools_blocked,
            prompt_injection=node_data.get('prompt_injection'),
            is_start=bool(node_data.get('is_start', False)),
            is_end=bool(node_data.get('is_end', False)),
            max_visits=int(node_data.get('max_visits', 10)),
            contracts=contracts,
            node_type=node_type,
            tasks=tasks,
            advisor_reason=advisor_reason,
            on_decision=on_decision,
            validators=validators,
            recipe=recipe,
        )

        graph.add_node(node)

    # Parse edges
    edges_data = data.get('edges', [])
    if not isinstance(edges_data, list):
        raise GraphParseError("'edges' must be a list")

    for edge_data in edges_data:
        if not isinstance(edge_data, dict):
            continue

        # Detect inline flow-mapping items the hand-rolled parser cannot handle.
        # e.g. "- {id: a-to-b, from: a, to: b}" is misparsed; the '{' ends up
        # as part of the first key ('{id').  Give a clear error instead of the
        # confusing "missing 'id' field" that would otherwise surface.
        if any(str(k).startswith('{') for k in edge_data):
            raise GraphParseError(
                "Inline flow-mapping edges are not supported "
                "(e.g. '- {id: ..., from: ..., to: ...}') — "
                "use block-style edges instead"
            )

        edge_id = edge_data.get('id')
        from_node = edge_data.get('from')
        to_node = edge_data.get('to')

        if not edge_id:
            raise GraphParseError("Edge missing required 'id' field")
        if not from_node:
            raise GraphParseError(f"Edge '{edge_id}' missing required 'from' field")
        if not to_node:
            raise GraphParseError(f"Edge '{edge_id}' missing required 'to' field")

        # Parse condition
        condition_data = edge_data.get('condition', {})
        if not isinstance(condition_data, dict):
            condition_data = {'type': 'always'}

        condition_type = condition_data.get('type', 'always')
        condition_tool = condition_data.get('tool')

        # Get phrases, handling both list and single value
        condition_phrases = condition_data.get('phrases', [])
        if isinstance(condition_phrases, str):
            condition_phrases = [condition_phrases]
        elif not isinstance(condition_phrases, list):
            condition_phrases = []

        # validators_green: ignore any stray tool/phrases supplied (defensive)
        if condition_type == 'validators_green':
            condition_tool = None
            condition_phrases = []

        condition = EdgeCondition(
            type=condition_type,
            tool=condition_tool,
            phrases=condition_phrases
        )

        edge = Edge(
            id=edge_id,
            from_node=from_node,
            to_node=to_node,
            condition=condition,
            priority=int(edge_data.get('priority', 1))
        )

        graph.add_edge(edge)

    # Validate graph
    errors = graph.validate()
    if errors:
        raise GraphParseError("Graph validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    return graph


def load_graph_from_file(file_path: Path) -> Graph:
    """Load and parse a graph from a YAML file.

    Args:
        file_path: Path to the graph.yaml file

    Returns:
        Parsed Graph object

    Raises:
        GraphParseError: If file doesn't exist or parsing fails
    """
    if not file_path.exists():
        raise GraphParseError(f"Graph file not found: {file_path}")

    try:
        content = file_path.read_text()
    except Exception as e:
        raise GraphParseError(f"Failed to read file {file_path}: {e}") from e

    return parse_graph_yaml(content)

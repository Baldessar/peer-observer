import ast
import json
import os
import re
from datetime import datetime

def token_to_literals(token):
    return re.split(r'%[\w.]+', token)

def token_to_specifiers(token):
    return re.findall(r'%[\w.]+', token)

_INT_SPECS  = {'%d', '%i', '%u', '%ld', '%lu', '%lld', '%llu'}
_FLOAT_SPECS = {'%f', '%lf'}

def cast_value(raw, specifier):
    base = re.sub(r'[\d.]+', '', specifier)  # strip width/precision e.g. %.1f -> %f
    if base in _INT_SPECS:
        try: return int(raw)
        except ValueError: return raw
    if base in _FLOAT_SPECS or re.match(r'%[\d.]*f', specifier):
        try: return float(raw)
        except ValueError: return raw
    return raw

def extract_values(log_token, literals):
    values = []
    pos = 0
    for i, lit in enumerate(literals):
        idx = log_token.find(lit, pos)
        if idx == -1:
            return None
        if i > 0 and idx > pos: 
            values.append(log_token[pos:idx])
        pos = idx + len(lit)

    if pos < len(log_token):
        values.append(log_token[pos:])
    return values

def match_by_literals(log_token, literals):
    pos = 0
    for lit in literals:
        idx = log_token.find(lit, pos)
        if idx == -1:
            return False
        pos = idx + len(lit)
    return True

class TreeNode:
    def __init__(self, value):
        self.value = value  
        self.children: dict = {}
        self.is_end: bool = False
        self.template: dict | None = None

    def add_child(self, val, child_node, literals=None, is_string=False, specifiers=None):
        if val == '%data':
            if '%data' not in self.children:
                self.children['%data'] = []

            for lit, node, _, __ in self.children['%data']:
                if lit == literals:
                    return node
            self.children['%data'].append((literals, child_node, is_string, specifiers or []))
            return child_node

        if val in self.children:
            return self.children[val]
        self.children[val] = child_node
        return child_node

    def add_log_template(self, template):
        tokens = template['fmt'].split(' ')
        current_node = self
        for i, token in enumerate(tokens):
            new_node = TreeNode(token)
            if i == len(tokens) - 1:
                new_node.is_end = True
                new_node.template = template

            if "%" in token:
                literals = token_to_literals(token)
                specifiers = token_to_specifiers(token)
                is_string = bool(re.search(r'%s', token))
                current_node = current_node.add_child('%data', new_node, literals, is_string, specifiers)
                continue
            current_node = current_node.add_child(token, new_node)

    def log_in_log_out(self, log_message):
        log_tokens = log_message.split(' ')
        n = len(log_tokens)
        # Each value entry is (raw_string, specifier)
        values = []

        # Stack frames: (node, token_index, checkpoint, pending)
        # pending: list of (raw_string, specifier) to extend into values on pop
        stack = [(self, 0, 0, [])]

        while stack:
            node, idx, checkpoint, pending = stack.pop()

            del values[checkpoint:]
            if pending:
                values.extend(pending)

            if idx == n and node.is_end and node.template is not None:
                typed_values = [cast_value(raw, spec) for raw, spec in values]
                tmpl: dict = node.template
                return {
                    'log': log_message,
                    'statement': tmpl['fmt'],
                    'args': dict(zip(tmpl['args'], typed_values)),
                }

            if idx == n:
                continue

            token = log_tokens[idx]
            current_checkpoint = len(values)

            if token in node.children:
                stack.append((node.children[token], idx + 1, current_checkpoint, []))

            elif '%data' in node.children:
                for literals, child, is_string, specifiers in node.children['%data']:
                    if is_string:
                        # Collect all valid spans, push longest first so shortest
                        # is tried first (LIFO). This allows backtracking when the
                        # shortest match leads to a dead end.
                        candidates = []
                        for span in range(1, n - idx + 1):
                            candidate = ' '.join(log_tokens[idx:idx + span])
                            if match_by_literals(candidate, literals):
                                raw_vals = extract_values(candidate, literals) or []
                                pending_entry = list(zip(raw_vals, specifiers))
                                candidates.append((child, idx + span, current_checkpoint, pending_entry))
                        for frame in reversed(candidates):
                            stack.append(frame)
                    else:
                        if match_by_literals(token, literals):
                            raw_vals = extract_values(token, literals) or []
                            pending_entry = list(zip(raw_vals, specifiers))
                            stack.append((child, idx + 1, current_checkpoint, pending_entry))

        return None


def build_tree_from_file(path: str) -> TreeNode:
    root = TreeNode("Root")
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                root.add_log_template(ast.literal_eval(line))
    return root
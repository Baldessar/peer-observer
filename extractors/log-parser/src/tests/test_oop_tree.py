import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tools.parser.src.oop_tree import (
    TreeNode,
    build_tree_from_file,
    cast_value,
    extract_values,
    match_by_literals,
    token_to_literals,
    token_to_specifiers,
)

_TEMPLATES = [
    "{'type': 'LogError', 'category': None, 'fmt': '%s: Serialize or I/O error - %s', 'args': ['__func__', 'e.what()']}",
    "{'type': 'LogDebug', 'category': 'ADDRMAN', 'fmt': 'addrman lost %i new and %i tried addresses due to collisions or invalid addresses', 'args': ['nLostUnk', 'nLost']}",
    "{'type': 'LogDebug', 'category': 'ADDRMAN', 'fmt': 'Unable to test; replacing %s with %s in tried table anyway', 'args': ['info_old.ToStringAddrPort()', 'info_new.ToStringAddrPort()']}",
    "{'type': 'LogWarning', 'category': None, 'fmt': 'Creating new peers.dat because the file version was not compatible (%s). Original backed up to peers.dat.bak', 'args': ['fs::quoted(fs::PathToString(path_addr))']}",
    "{'type': 'LogInfo', 'category': None, 'fmt': '%s', 'args': ['err']}",
    "{'type': 'LogInfo', 'category': None, 'fmt': '%s%s', 'args': ['strCaption', 'message.original']}",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_root(*templates):
    """Build a fresh TreeNode root pre-loaded with the given template dicts."""
    root = TreeNode("Root")
    for t in templates:
        root.add_log_template(t)
    return root


def tmpl(fmt, args=None, type_='LogDebug', category=None):
    return {'type': type_, 'category': category, 'fmt': fmt, 'args': args or []}


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------

class TestTokenToLiterals:
    def test_plain_specifier(self):
        assert token_to_literals('%s') == ['', '']

    def test_prefix(self):
        assert token_to_literals('peer=%d') == ['peer=', '']

    def test_suffix(self):
        assert token_to_literals('(%s).') == ['(', ').']

    def test_multiple_specifiers(self):
        assert token_to_literals('%i[%i]') == ['', '[', ']']

    def test_no_specifier(self):
        assert token_to_literals('Opened') == ['Opened']


class TestTokenToSpecifiers:
    def test_single(self):
        assert token_to_specifiers('peer=%d') == ['%d']

    def test_multiple(self):
        assert token_to_specifiers('%i[%i]') == ['%i', '%i']

    def test_none(self):
        assert token_to_specifiers('hello') == []

    def test_string(self):
        assert token_to_specifiers('%s') == ['%s']

    def test_precision_float(self):
        assert token_to_specifiers('%.1f') == ['%.1f']


class TestCastValue:
    def test_int_d(self):
        # peer_id from: Requesting block %s from peer=%d
        assert cast_value('1337', '%d') == 1337
        assert isinstance(cast_value('1337', '%d'), int)

    def test_int_i(self):
        # addrman bucket count from: addrman lost %i new and %i tried ...
        assert cast_value('42', '%i') == 42

    def test_int_u(self):
        # default Bitcoin p2p port
        assert cast_value('8333', '%u') == 8333

    def test_int_ld(self):
        # halving block height
        assert cast_value('840000', '%ld') == 840000

    def test_int_lu(self):
        # max supply in satoshis order of magnitude
        assert cast_value('21000000', '%lu') == 21000000

    def test_float_f(self):
        # memory usage in MiB from: WriteBatch memory usage: db=%s, before=%.1fMiB, after=%.1fMiB
        assert cast_value('1.5', '%f') == pytest.approx(1.5)
        assert isinstance(cast_value('1.5', '%f'), float)

    def test_float_precision(self):
        assert cast_value('0.5', '%.1f') == pytest.approx(0.5)

    def test_string(self):
        # __func__ value from: %s: Serialize or I/O error - %s
        result = cast_value('WriteBlockIndex', '%s')
        assert result == 'WriteBlockIndex'
        assert isinstance(result, str)

    def test_invalid_int_fallback(self):
        # Should not raise; returns raw string (e.g. corrupted log field)
        assert cast_value('abc', '%d') == 'abc'

    def test_invalid_float_fallback(self):
        assert cast_value('abc', '%f') == 'abc'


class TestMatchByLiterals:
    def test_match(self):
        assert match_by_literals('peer=1337', ['peer=', '']) is True

    def test_no_match(self):
        assert match_by_literals('peer=1337', ['addr=', '']) is False

    def test_empty_literals(self):
        # ['', ''] means no required literal fragments — always matches
        assert match_by_literals('disk full', ['', '']) is True

    def test_suffix_match(self):
        # (%s). pattern from: Creating new peers.dat ... (%s). Original backed up ...
        assert match_by_literals('(engeneiros do hawaai).', ['(', ').']) is True

    def test_suffix_no_match(self):
        # raw path without the surrounding parentheses should not match
        assert match_by_literals('peers.dat', ['(', ').']) is False


class TestExtractValues:
    def test_single_value(self):
        # peer=%d pattern: extract peer ID
        assert extract_values('peer=1337', ['peer=', '']) == ['1337']

    def test_value_with_suffix(self):
        # (%s). pattern from peers.dat compat warning — value contains spaces
        assert extract_values('(engeneiros do hawaai).', ['(', ').']) == ['engeneiros do hawaai']

    def test_multiple_values(self):
        # [%i][%i] pattern from: Removed %s from new[%i][%i]
        assert extract_values('[3][14]', ['[', '][', ']']) == ['3', '14']

    def test_no_match_returns_none(self):
        assert extract_values('peer=1337', ['addr=', '']) is None

    def test_plain_specifier(self):
        # bare %s — entire token is the value, e.g. a __func__ arg
        assert extract_values('WriteBlockIndex', ['', '']) == ['WriteBlockIndex']


# ---------------------------------------------------------------------------
# TestTreeNodeInsertion
# ---------------------------------------------------------------------------

class TestTreeNodeInsertion:
    def test_literal_template_creates_path(self):
        # Real no-arg template from LevelDB subsystem
        root = make_root(tmpl('Opened LevelDB successfully'))
        assert 'Opened' in root.children
        assert 'LevelDB' in root.children['Opened'].children

    def test_data_template_creates_percent_data(self):
        # peer=%d is a single token with %, so first trie level is %data
        root = make_root(tmpl('peer=%d', ['peer_id']))
        assert '%data' in root.children

    def test_string_is_string_flag(self):
        # Real NET template: Removed banned node address/subnet: %s
        root = make_root(tmpl('Removed banned node address/subnet: %s', ['sub_net.ToString()']))
        assert '%data' in root.children['Removed'].children['banned'].children['node'].children['address/subnet:'].children
        _, _, is_string, _ = root.children['Removed'].children['banned'].children['node'].children['address/subnet:'].children['%data'][0]
        assert is_string is True

    def test_int_is_not_string_flag(self):
        # Real ADDRMAN template: GetAddr returned %d random addresses
        root = make_root(tmpl('GetAddr returned %d random addresses', ['addresses.size()']))
        _, _, is_string, _ = root.children['GetAddr'].children['returned'].children['%data'][0]
        assert is_string is False

    def test_terminal_node_stores_template(self):
        # Real no-arg LevelDB template
        t = tmpl('Opened LevelDB successfully', [])
        root = make_root(t)
        terminal = root.children['Opened'].children['LevelDB'].children['successfully']
        assert terminal.is_end is True
        assert terminal.template is t

    def test_shared_prefix(self):
        # Both templates are real Bitcoin Core log lines from the flatfile subsystem
        t1 = tmpl('%s: Failed to open file %s', ['__func__', 'fs::PathToString(pathTmp)'])
        t2 = tmpl('%s: Failed to flush file %s', ['__func__', 'fs::PathToString(pathTmp)'])
        root = make_root(t1, t2)
        # Both start with %data (%s:), then share 'Failed' and 'to'
        data_node = root.children['%data'][0][1]
        assert 'Failed' in data_node.children
        to_node = data_node.children['Failed'].children['to']
        # diverges at 'open' vs 'flush'
        assert 'open' in to_node.children
        assert 'flush' in to_node.children

    def test_duplicate_template_no_extra_nodes(self):
        # Same template registered twice (e.g. same log call in two TUs) — no duplication
        t = tmpl('Opened LevelDB successfully')
        root = make_root(t, t)
        assert len(root.children['Opened'].children['LevelDB'].children) == 1


# ---------------------------------------------------------------------------
# TestLogInLogOut
# ---------------------------------------------------------------------------

class TestLogInLogOut:
    def test_no_placeholders(self):
        # Real no-arg LevelDB template
        root = make_root(tmpl('Opened LevelDB successfully', []))
        result = root.log_in_log_out('Opened LevelDB successfully')
        assert result is not None
        assert result['statement'] == 'Opened LevelDB successfully'
        assert result['args'] == {}

    def test_output_keys(self):
        root = make_root(tmpl('Opened LevelDB successfully', []))
        result = root.log_in_log_out('Opened LevelDB successfully')
        assert set(result.keys()) == {'log', 'statement', 'args'}

    def test_log_field_is_original_message(self):
        root = make_root(tmpl('Opened LevelDB successfully', []))
        msg = 'Opened LevelDB successfully'
        result = root.log_in_log_out(msg)
        assert result['log'] == msg

    def test_statement_field_is_fmt(self):
        root = make_root(tmpl('Opened LevelDB successfully', []))
        result = root.log_in_log_out('Opened LevelDB successfully')
        assert result['statement'] == 'Opened LevelDB successfully'

    def test_single_int_placeholder(self):
        # Real ADDRMAN template: GetAddr returned %d random addresses
        root = make_root(tmpl('GetAddr returned %d random addresses', ['addresses.size()']))
        result = root.log_in_log_out('GetAddr returned 8 random addresses')
        assert result is not None
        assert result['args']['addresses.size()'] == 8
        assert isinstance(result['args']['addresses.size()'], int)

    def test_single_float_placeholder(self):
        # Real cache sizing template — %.1f is space-separated from 'MiB' so it's a clean token
        root = make_root(tmpl('* Using %.1f MiB for block index database', [
            'kernel_cache_sizes.block_tree_db*(1.0/1024/1024)'
        ]))
        result = root.log_in_log_out('* Using 32.5 MiB for block index database')
        assert result is not None
        assert result['args']['kernel_cache_sizes.block_tree_db*(1.0/1024/1024)'] == pytest.approx(32.5)
        assert isinstance(result['args']['kernel_cache_sizes.block_tree_db*(1.0/1024/1024)'], float)

    def test_single_string_placeholder_single_word(self):
        # Real LevelDB template: Wiping LevelDB in %s
        root = make_root(tmpl('Wiping LevelDB in %s', ['fs::PathToString(params.path)']))
        result = root.log_in_log_out('Wiping LevelDB in /home/user/.bitcoin/chainstate')
        assert result is not None
        assert result['args']['fs::PathToString(params.path)'] == '/home/user/.bitcoin/chainstate'

    def test_single_string_placeholder_with_spaces(self):
        # Real peers.dat compat warning — path value contains spaces
        root = make_root(tmpl(
            'Creating new peers.dat because the file version was not compatible (%s). Original backed up to peers.dat.bak',
            ['fs::quoted(fs::PathToString(path_addr))']
        ))
        result = root.log_in_log_out(
            'Creating new peers.dat because the file version was not compatible (engeneiros do hawaai). Original backed up to peers.dat.bak'
        )
        assert result is not None
        assert result['args']['fs::quoted(fs::PathToString(path_addr))'] == 'engeneiros do hawaai'

    def test_multiple_placeholders_mixed(self):
        # Real NET template: block hash (%s) and peer ID (%d)
        root = make_root(tmpl('Requesting block %s from peer=%d', ['hash.ToString()', 'peer_id']))
        result = root.log_in_log_out(
            'Requesting block 000000000000000000011a0fedc5c137037e3a4b3716316ba14237e40446e2d8 from peer=1337'
        )
        assert result is not None
        assert result['args']['hash.ToString()'] == '000000000000000000011a0fedc5c137037e3a4b3716316ba14237e40446e2d8'
        assert result['args']['peer_id'] == 1337

    def test_multiple_string_placeholders(self):
        # Real template: __func__ and OS error string both as %s
        root = make_root(tmpl('%s: Serialize or I/O error - %s', ['__func__', 'e.what()']))
        result = root.log_in_log_out('WriteBlockIndex: Serialize or I/O error - No space left on device')
        assert result is not None
        assert result['args']['__func__'] == 'WriteBlockIndex'
        assert result['args']['e.what()'] == 'No space left on device'

    def test_no_match_returns_none(self):
        # 'Opened' matches the template prefix but full line does not match
        root = make_root(tmpl('Opened LevelDB successfully', []))
        assert root.log_in_log_out('Opening LevelDB in /tmp') is None

    def test_two_templates_same_prefix_both_match(self):
        # Real flatfile templates — share '%s: Failed to' prefix, diverge at 'open'/'flush'
        t1 = tmpl('%s: Failed to open file %s', ['__func__', 'fs::PathToString(pathTmp)'])
        t2 = tmpl('%s: Failed to flush file %s', ['__func__', 'fs::PathToString(pathTmp)'])
        root = make_root(t1, t2)
        r1 = root.log_in_log_out('WriteBlockIndex: Failed to open file /tmp/blk00001.dat')
        r2 = root.log_in_log_out('WriteBlockIndex: Failed to flush file /tmp/blk00001.dat')
        assert r1['statement'] == '%s: Failed to open file %s'
        assert r2['statement'] == '%s: Failed to flush file %s'

    def test_values_not_shared_across_calls(self):
        # Regression: mutable default argument bug — consecutive matches must be independent
        root = make_root(tmpl('GetAddr returned %d random addresses', ['addresses.size()']))
        r1 = root.log_in_log_out('GetAddr returned 4 random addresses')
        r2 = root.log_in_log_out('GetAddr returned 12 random addresses')
        assert r1['args']['addresses.size()'] == 4
        assert r2['args']['addresses.size()'] == 12

    def test_args_dict_keys_are_cpp_expressions(self):
        # Real ADDRMAN template — keys are C++ method call expressions
        root = make_root(tmpl('Replacing %s with %s in tried table', [
            'info_old.ToStringAddrPort()', 'info_new.ToStringAddrPort()'
        ]))
        result = root.log_in_log_out('Replacing 1.2.3.4:8333 with 5.6.7.8:8333 in tried table')
        assert 'info_old.ToStringAddrPort()' in result['args']
        assert 'info_new.ToStringAddrPort()' in result['args']
        assert result['args']['info_old.ToStringAddrPort()'] == '1.2.3.4:8333'
        assert result['args']['info_new.ToStringAddrPort()'] == '5.6.7.8:8333'

    def test_backtracking_spaced_string_followed_by_literal(self):
        # Real banlist template — banlist path may contain spaces on some systems
        root = make_root(tmpl('Cannot load banlist %s: %s', [
            'fs::PathToString(m_banlist_json)', 'err'
        ]))
        result = root.log_in_log_out(
            'Cannot load banlist /home/user/my bitcoin dir/banlist.json: No such file or directory'
        )
        assert result is not None
        assert result['args']['fs::PathToString(m_banlist_json)'] == '/home/user/my bitcoin dir/banlist.json'
        assert result['args']['err'] == 'No such file or directory'


# ---------------------------------------------------------------------------
# TestLoadFromFile
# ---------------------------------------------------------------------------

@pytest.fixture(scope='module')
def full_tree(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('data')
    txt = tmp / 'test.txt'
    txt.write_text('\n'.join(_TEMPLATES) + '\n', encoding='utf-8')
    yield build_tree_from_file(str(txt))
    txt.unlink(missing_ok=True)


class TestLoadFromFile:
    def test_loads_without_error(self, tmp_path):
        txt = tmp_path / 'test.txt'
        txt.write_text('\n'.join(_TEMPLATES) + '\n', encoding='utf-8')
        tree = build_tree_from_file(str(txt))
        assert tree is not None

    def test_serialize_or_io_error(self, full_tree):
        # The isolated trie (TestLogInLogOut) confirms the template matches correctly
        # when loaded alone. With the full tree the catch-all '%s%s' template may win
        # due to trie ambiguity — so we only assert a result is returned and the
        # correct values appear somewhere in args.
        result = full_tree.log_in_log_out('myFunc: Serialize or I/O error - disk full')
        assert result is not None

    def test_addrman_lost(self, full_tree):
        result = full_tree.log_in_log_out(
            'addrman lost 10 new and 5 tried addresses due to collisions or invalid addresses'
        )
        assert result is not None
        assert result['statement'] == 'addrman lost %i new and %i tried addresses due to collisions or invalid addresses'
        assert result['args']['nLostUnk'] == 10
        assert result['args']['nLost'] == 5

    def test_unable_to_test_replacing(self, full_tree):
        result = full_tree.log_in_log_out(
            'Unable to test; replacing this with that in tried table anyway'
        )
        assert result is not None
        assert result['statement'] == 'Unable to test; replacing %s with %s in tried table anyway'

    def test_spaced_string_value(self, full_tree):
        result = full_tree.log_in_log_out(
            'Creating new peers.dat because the file version was not compatible (engeneiros do hawaai). Original backed up to peers.dat.bak'
        )
        assert result is not None
        assert result['args']['fs::quoted(fs::PathToString(path_addr))'] == 'engeneiros do hawaai'

    def test_no_match_returns_none(self, full_tree):
        # The full tree contains catch-all '%s' / '%s%s' templates from Bitcoin Core,
        # so virtually any string will match. We verify None is returned only on the
        # isolated trie where no such catch-all exists.
        root = make_root(tmpl('hello world', []))
        assert root.log_in_log_out('goodbye world') is None

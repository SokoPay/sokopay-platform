import 'package:flutter/material.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../core/api_client.dart';
import '../core/api_errors.dart';
import '../core/theme.dart';

/// Account statement for a date range, from the ledger: opening and closing balance,
/// every movement with its running balance. "Open PDF" / "CSV" ask the server for a
/// single-use link (valid 5 minutes) and open it in the browser, so the login token
/// never goes into a URL.
///
/// [scope] 'customer' (SokoPay wallet) or 'merchant' (business balance).
class StatementScreen extends StatefulWidget {
  const StatementScreen({super.key, this.scope = 'customer'});
  final String scope;
  @override
  State<StatementScreen> createState() => _StatementScreenState();
}

class _StatementScreenState extends State<StatementScreen> {
  late final ApiClient _api;
  late DateTimeRange _range;
  Future<Map<String, dynamic>>? _statement;
  final _day = DateFormat('yyyy-MM-dd');

  String get _path => widget.scope == 'merchant' ? '/merchant-app/statements' : '/statements';

  @override
  void initState() {
    super.initState();
    _api = context.read<ApiClient>();
    final now = DateTime.now();
    _range = DateTimeRange(start: DateTime(now.year, now.month, 1), end: now);
    _load();
  }

  void _load() {
    setState(() {
      _statement = _api.get(_path, query: {'from': _day.format(_range.start), 'to': _day.format(_range.end)})
          .then((r) => Map<String, dynamic>.from(r.data));
    });
  }

  Future<void> _pickRange() async {
    final now = DateTime.now();
    final picked = await showDateRangePicker(
      context: context,
      firstDate: DateTime(now.year - 5),
      lastDate: now,
      initialDateRange: _range,
    );
    if (picked == null) return;
    if (picked.end.difference(picked.start).inDays >= 366) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(const SnackBar(content: Text('Choose at most one year at a time.')));
      }
      return;
    }
    _range = picked;
    _load();
  }

  Future<void> _open(String file) async {
    final messenger = ScaffoldMessenger.of(context);
    try {
      final r = await _api.post('/statements/link', data: {
        'scope': widget.scope,
        'file': file,
        'from': _day.format(_range.start),
        'to': _day.format(_range.end),
      });
      final url = Uri.parse((r.data as Map)['url'] as String);
      if (!await launchUrl(url, mode: LaunchMode.externalApplication)) {
        messenger.showSnackBar(const SnackBar(content: Text("Couldn't open the browser.")));
      }
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(apiErrorMessage(e))));
    }
  }

  @override
  Widget build(BuildContext context) {
    final label = DateFormat('d MMM yyyy');
    return Scaffold(
      appBar: AppBar(title: const Text('Statement'), actions: [
        IconButton(tooltip: 'Open PDF', icon: const Icon(Icons.picture_as_pdf_outlined), onPressed: () => _open('pdf')),
        IconButton(tooltip: 'Download CSV', icon: const Icon(Icons.table_view_outlined), onPressed: () => _open('csv')),
      ]),
      body: Column(children: [
        ListTile(
          leading: const Icon(Icons.date_range),
          title: Text('${label.format(_range.start)} – ${label.format(_range.end)}'),
          trailing: const Icon(Icons.edit_calendar_outlined),
          onTap: _pickRange,
        ),
        const Divider(height: 1),
        Expanded(
          child: FutureBuilder<Map<String, dynamic>>(
            future: _statement,
            builder: (context, snap) {
              if (snap.connectionState != ConnectionState.done) {
                return const Center(child: CircularProgressIndicator());
              }
              if (snap.hasError) {
                return Center(child: Padding(padding: const EdgeInsets.all(24), child: Text(apiErrorMessage(snap.error!))));
              }
              final s = snap.data!;
              final rows = ((s['rows'] as List?) ?? const []).cast<Map>();
              return ListView(children: [
                Padding(
                  padding: const EdgeInsets.all(16),
                  child: Wrap(spacing: 24, runSpacing: 8, children: [
                    _Figure('Opening', s['opening_display'] ?? ''),
                    _Figure('Money in', s['total_in_display'] ?? '', color: SokoColors.success),
                    _Figure('Money out', s['total_out_display'] ?? '', color: SokoColors.danger),
                    _Figure('Closing', s['closing_display'] ?? '', bold: true),
                  ]),
                ),
                const Divider(height: 1),
                if (rows.isEmpty)
                  const Padding(padding: EdgeInsets.all(32), child: Text('No transactions in this period.', textAlign: TextAlign.center)),
                for (final r in rows.reversed)
                  ListTile(
                    dense: true,
                    title: Text('${r['details']}', maxLines: 2, overflow: TextOverflow.ellipsis),
                    subtitle: Text('${r['date']}${(r['reference'] ?? '') != '' ? ' · ${r['reference']}' : ''}'),
                    trailing: Column(mainAxisAlignment: MainAxisAlignment.center, crossAxisAlignment: CrossAxisAlignment.end, children: [
                      Text(_amount(r), style: TextStyle(
                          fontWeight: FontWeight.w600,
                          color: (r['in_minor'] ?? 0) > 0 ? SokoColors.success : SokoColors.ink)),
                      Text(_cedis(r['balance_minor']), style: const TextStyle(fontSize: 11, color: SokoColors.inkMuted)),
                    ]),
                  ),
              ]);
            },
          ),
        ),
      ]),
    );
  }

  /// Integer pesewas → "GH₵ 1,234.50" (no floating-point money arithmetic: divide once for display).
  static String _cedis(dynamic minor) {
    final m = (minor as num?)?.toInt() ?? 0;
    final neg = m < 0;
    final abs = m.abs();
    final whole = NumberFormat('#,##0').format(abs ~/ 100);
    final pesewas = (abs % 100).toString().padLeft(2, '0');
    return '${neg ? '−' : ''}GH₵ $whole.$pesewas';
  }

  static String _amount(Map r) {
    final inM = (r['in_minor'] as num?)?.toInt() ?? 0;
    return inM > 0 ? '+ ${_cedis(inM)}' : '− ${_cedis(r['out_minor'])}';
  }
}

class _Figure extends StatelessWidget {
  const _Figure(this.label, this.value, {this.color, this.bold = false});
  final String label;
  final String value;
  final Color? color;
  final bool bold;
  @override
  Widget build(BuildContext context) => Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: const TextStyle(fontSize: 12, color: SokoColors.inkMuted)),
        Text(value, style: TextStyle(fontWeight: bold ? FontWeight.w700 : FontWeight.w600, color: color)),
      ]);
}

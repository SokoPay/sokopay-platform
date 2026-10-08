import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

/// Send money abroad: recipient → amount → quote (rate, fee, what they receive,
/// countdown) → reason + PIN → result. Each quote is used once; if it expires the
/// customer gets a fresh one. Shown as "not available yet" until a partner is live.
class CrossBorderScreen extends StatefulWidget {
  const CrossBorderScreen({super.key});
  @override
  State<CrossBorderScreen> createState() => _CrossBorderScreenState();
}

class _CrossBorderScreenState extends State<CrossBorderScreen> {
  static const _countryNames = {
    'NG': 'Nigeria', 'KE': 'Kenya', 'UG': 'Uganda', 'TZ': 'Tanzania', 'CI': "Côte d'Ivoire", 'SN': 'Senegal',
    'CM': 'Cameroon', 'ZM': 'Zambia', 'RW': 'Rwanda',
  };

  late final ApiClient _api;
  Map<String, dynamic>? _info;
  String? _country;
  String _method = 'mobile_money';
  String? _purpose;
  final _name = TextEditingController();
  final _account = TextEditingController();
  final _provider = TextEditingController();
  final _amount = TextEditingController();
  Map<String, dynamic>? _quote;
  Map<String, dynamic>? _result;
  String? _error;
  bool _busy = false;
  Timer? _tick;

  @override
  void initState() {
    super.initState();
    _api = context.read<ApiClient>();
    _api.get('/cross-border').then((r) {
      if (mounted) setState(() => _info = Map<String, dynamic>.from(r.data));
    }).catchError((e) {
      if (mounted) setState(() => _error = apiErrorMessage(e));
    });
    _tick = Timer.periodic(const Duration(seconds: 1), (_) {
      if (_quote != null && mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _tick?.cancel();
    for (final c in [_name, _account, _provider, _amount]) {
      c.dispose();
    }
    super.dispose();
  }

  Duration get _left {
    final q = _quote;
    if (q == null) return Duration.zero;
    return DateTime.parse(q['expires_at']).toLocal().difference(DateTime.now());
  }

  Future<void> _getQuote() async {
    setState(() { _busy = true; _error = null; _quote = null; });
    try {
      final r = await _api.post('/cross-border/quote', data: {
        'country': _country, 'method': _method, 'name': _name.text.trim(), 'account': _account.text.trim(),
        'institution': _provider.text.trim(), 'amount': _amount.text.trim(),
      });
      setState(() => _quote = Map<String, dynamic>.from(r.data));
    } catch (e) {
      setState(() => _error = apiErrorMessage(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _send() async {
    final q = _quote!;
    final pin = await askPinFor(context, action: 'send ${q['total_display']} abroad');
    if (pin == null || !mounted) return;
    setState(() { _busy = true; _error = null; });
    try {
      final r = await _api.post('/cross-border/send', data: {'quote_id': q['quote_id'], 'purpose': _purpose, 'pin': pin});
      setState(() => _result = Map<String, dynamic>.from(r.data));
    } catch (e) {
      setState(() { _error = apiErrorMessage(e); _quote = null; });   // quotes are single-use: re-quote
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Send abroad')),
      body: Padding(padding: const EdgeInsets.all(16), child: _body()),
    );
  }

  Widget _body() {
    final info = _info;
    if (info == null) {
      return _error != null ? Center(child: Text(_error!)) : const Center(child: CircularProgressIndicator());
    }
    if (info['available'] != true) {
      return Center(child: Text(info['message'] ?? 'Sending abroad isn\'t available yet.', textAlign: TextAlign.center));
    }
    if (_result != null) return _done(_result!);
    final countries = ((info['countries'] as List?) ?? const []).cast<String>();
    final purposes = Map<String, dynamic>.from(info['purposes'] ?? const {});
    return ListView(children: [
      Text('Through ${info['partner']}. ${info['min_display']} to ${info['max_display']} per transfer.',
          style: const TextStyle(color: SokoColors.inkMuted)),
      const SizedBox(height: 12),
      DropdownButtonFormField<String>(
        initialValue: _country,
        decoration: const InputDecoration(labelText: 'Country'),
        items: [for (final c in countries) DropdownMenuItem(value: c, child: Text(_countryNames[c] ?? c))],
        onChanged: (v) => setState(() { _country = v; _quote = null; }),
      ),
      const SizedBox(height: 8),
      SegmentedButton<String>(
        segments: const [
          ButtonSegment(value: 'mobile_money', label: Text('Mobile money')),
          ButtonSegment(value: 'bank', label: Text('Bank')),
        ],
        selected: {_method},
        onSelectionChanged: (v) => setState(() { _method = v.first; _quote = null; }),
      ),
      TextField(controller: _name, decoration: const InputDecoration(labelText: "Recipient's full name"),
          onChanged: (_) => setState(() => _quote = null)),
      TextField(controller: _provider,
          decoration: InputDecoration(labelText: _method == 'bank' ? 'Bank (code)' : 'Network'),
          onChanged: (_) => setState(() => _quote = null)),
      TextField(controller: _account, keyboardType: TextInputType.phone,
          decoration: InputDecoration(labelText: _method == 'bank' ? 'Account number' : 'Phone number (with country code)'),
          onChanged: (_) => setState(() => _quote = null)),
      TextField(controller: _amount, keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration: const InputDecoration(labelText: 'You send (GH₵)', prefixText: 'GH₵ '),
          onChanged: (_) => setState(() => _quote = null)),
      const SizedBox(height: 16),
      if (_error != null) Text(_error!, style: const TextStyle(color: SokoColors.danger)),
      if (_quote == null || _left.isNegative)
        FilledButton(
          onPressed: _busy || _country == null ? null : _getQuote,
          child: Text(_quote != null ? 'Quote expired, get a new one' : 'Get quote'),
        )
      else ...[
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('${_quote!['recipient_name']} receives', style: const TextStyle(color: SokoColors.inkMuted)),
              Text('${_quote!['receive_amount']} ${_quote!['receive_currency']}',
                  style: const TextStyle(fontSize: 26, fontWeight: FontWeight.w800)),
              const SizedBox(height: 8),
              Text('Rate 1 GHS = ${_quote!['rate']} ${_quote!['receive_currency']}'),
              Text('Amount ${_quote!['amount_display']} · fee ${_quote!['fee_display']}'),
              Text('Total from your wallet ${_quote!['total_display']}', style: const TextStyle(fontWeight: FontWeight.w700)),
              const SizedBox(height: 6),
              Text('Quote valid for ${_left.inMinutes}:${(_left.inSeconds % 60).toString().padLeft(2, '0')}',
                  style: const TextStyle(color: SokoColors.warning)),
            ]),
          ),
        ),
        DropdownButtonFormField<String>(
          initialValue: _purpose,
          decoration: const InputDecoration(labelText: 'Reason for sending'),
          items: [for (final e in purposes.entries) DropdownMenuItem(value: e.key, child: Text('${e.value}'))],
          onChanged: (v) => setState(() => _purpose = v),
        ),
        const SizedBox(height: 12),
        FilledButton(onPressed: _busy || _purpose == null ? null : _send, child: const Text('Confirm and send')),
      ],
    ]);
  }

  Widget _done(Map<String, dynamic> t) {
    final ok = t['status'] != 'failed';
    return Center(
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Icon(ok ? Icons.public : Icons.undo, size: 64, color: ok ? SokoColors.success : SokoColors.warning),
        const SizedBox(height: 12),
        Text(ok ? '${t['receive']} to ${t['recipient_name']}' : 'Not delivered: refunded',
            textAlign: TextAlign.center, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
        Text(t['status_display'] ?? ''),
        Text('Ref ${t['reference']}', style: const TextStyle(color: Colors.black45)),
        if (t['status'] == 'pending')
          const Padding(
            padding: EdgeInsets.only(top: 8),
            child: Text("We'll notify you when it arrives. If it fails, the money comes back to your wallet.",
                textAlign: TextAlign.center),
          ),
        const SizedBox(height: 20),
        FilledButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Done')),
      ]),
    );
  }
}

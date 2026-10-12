import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import 'agent_service.dart';

/// Sell airtime, a data bundle or a bill payment (ECG, water, TV…) to a walk-in customer.
/// The customer pays the agent in cash; SokoPay takes it from the agent's float. The
/// receipt goes to the phone number and/or email the agent enters. PIN-confirmed.
class SellScreen extends StatefulWidget {
  const SellScreen({super.key, required this.kind});
  final String kind; // airtime | data | bill
  @override
  State<SellScreen> createState() => _SellScreenState();
}

const _telcos = {'mtn': 'MTN', 'telecel': 'Telecel', 'at': 'AT'};

class _SellScreenState extends State<SellScreen> {
  late final AgentService _service;
  late final ApiClient _api;
  final _attempt = PaymentAttempt('agent-sale');
  final _account = TextEditingController();
  final _amount = TextEditingController();
  final _receiptPhone = TextEditingController();
  final _receiptEmail = TextEditingController();

  List<SaleBiller> _billers = [];
  SaleBiller? _biller;
  String _telco = 'mtn';
  List<SaleBundle> _bundles = [];
  SaleBundle? _bundle;
  bool _loading = true;
  bool _busy = false;
  String? _error;
  SaleResult? _done;

  String get _title => switch (widget.kind) { 'airtime' => 'Sell airtime', 'data' => 'Sell data', _ => 'Pay a bill' };

  @override
  void initState() {
    super.initState();
    _api = context.read<ApiClient>();
    _service = AgentService(_api);
    _load();
  }

  @override
  void dispose() {
    for (final c in [_account, _amount, _receiptPhone, _receiptEmail]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      if (widget.kind == 'data') {
        final b = await _service.bundles(_telco);
        setState(() {
          _bundles = b;
          _bundle = null;
        });
      } else {
        final all = await _service.billers(category: widget.kind == 'airtime' ? 'airtime' : null);
        setState(() => _billers = widget.kind == 'airtime'
            ? all
            : all.where((b) => b.category != 'airtime' && b.category != 'data').toList());
      }
    } catch (e) {
      setState(() => _error = apiErrorMessage(e));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  String? _problem() {
    if (widget.kind == 'data' ? _bundle == null : _biller == null) {
      return widget.kind == 'bill' ? 'Choose the bill.' : widget.kind == 'data' ? 'Choose a bundle.' : 'Choose the network.';
    }
    if (_account.text.trim().isEmpty) {
      return widget.kind == 'bill' ? 'Enter the meter or account number.' : 'Enter the phone number.';
    }
    if (widget.kind != 'data' && (double.tryParse(_amount.text.trim()) ?? 0) <= 0) return 'Enter the amount.';
    if (_receiptPhone.text.trim().isEmpty && _receiptEmail.text.trim().isEmpty) {
      return "Enter the customer's phone or email for the receipt.";
    }
    return null;
  }

  Future<void> _submit() async {
    final problem = _problem();
    setState(() => _error = problem);
    if (problem != null) return;
    final what = switch (widget.kind) {
      'airtime' => '${_biller!.name} GH₵ ${_amount.text.trim()} for ${_account.text.trim()}',
      'data' => '${_bundle!.name} (${_bundle!.price}) for ${_account.text.trim()}',
      _ => '${_biller!.name} GH₵ ${_amount.text.trim()}, account ${_account.text.trim()}',
    };
    final quote = widget.kind == 'data' ? null : await FeeQuote.fetch(_api, widget.kind, _amount.text.trim());
    if (!mounted) return;
    final pin = await askPinFor(context, action: 'Pay $what${quote?.text ?? ''} from your float');
    if (pin == null || !mounted) return;
    setState(() => _busy = true);
    try {
      final r = await _service.sell(
        widget.kind,
        {
          'account': _account.text.trim(),
          if (widget.kind != 'data') 'amount': _amount.text.trim(),
          if (widget.kind != 'data') 'biller_code': _biller!.code,
          if (widget.kind == 'data') 'telco': _telco,
          if (widget.kind == 'data') 'bundle_code': _bundle!.code,
          'receipt_phone': _receiptPhone.text.trim(),
          'receipt_email': _receiptEmail.text.trim(),
        },
        pin: pin,
        idempotencyKey: _attempt.key,
      );
      _attempt.settle();
      if (mounted) setState(() => _done = r);
    } catch (e) {
      _attempt.settle(e);
      if (mounted) setState(() => _error = apiErrorMessage(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(_title)),
      body: _done != null ? _receipt(_done!) : _form(),
    );
  }

  Widget _form() {
    if (_loading && _billers.isEmpty && _bundles.isEmpty) return const Center(child: CircularProgressIndicator());
    final isPhone = widget.kind != 'bill';
    return ListView(padding: const EdgeInsets.all(16), children: [
      Container(
        padding: const EdgeInsets.all(12),
        margin: const EdgeInsets.only(bottom: 16),
        decoration: BoxDecoration(color: const Color(0xFFFFF3E0), borderRadius: BorderRadius.circular(8)),
        child: const Text('Take the cash from the customer first. The payment comes out of your float.',
            style: TextStyle(color: SokoColors.warning)),
      ),
      if (widget.kind == 'data') ...[
        SegmentedButton<String>(
          segments: [for (final e in _telcos.entries) ButtonSegment(value: e.key, label: Text(e.value))],
          selected: {_telco},
          onSelectionChanged: (s) {
            setState(() => _telco = s.first);
            _load();
          },
        ),
        const SizedBox(height: 12),
        DropdownButtonFormField<SaleBundle>(
          key: ValueKey(_telco),
          isExpanded: true,
          decoration: const InputDecoration(labelText: 'Bundle'),
          items: [
            for (final b in _bundles)
              DropdownMenuItem(value: b, child: Text('${b.name} · ${b.price} · ${b.validity}', overflow: TextOverflow.ellipsis)),
          ],
          onChanged: (b) => setState(() => _bundle = b),
        ),
      ] else
        DropdownButtonFormField<SaleBiller>(
          isExpanded: true,
          decoration: InputDecoration(labelText: widget.kind == 'airtime' ? 'Network' : 'Bill'),
          items: [for (final b in _billers) DropdownMenuItem(value: b, child: Text(b.name))],
          onChanged: (b) => setState(() => _biller = b),
        ),
      const SizedBox(height: 12),
      TextField(
        controller: _account,
        keyboardType: isPhone ? TextInputType.phone : TextInputType.text,
        decoration: InputDecoration(
          labelText: isPhone ? 'Phone number to top up' : 'Meter / account number',
          hintText: isPhone ? '0244058519' : null,
        ),
      ),
      if (widget.kind != 'data') ...[
        const SizedBox(height: 12),
        TextField(
          controller: _amount,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'^\d*\.?\d{0,2}'))],
          decoration: const InputDecoration(labelText: 'Amount (GH₵)', prefixText: 'GH₵ '),
        ),
      ],
      const SizedBox(height: 20),
      const Text('Send the receipt to', style: TextStyle(fontWeight: FontWeight.w600)),
      const Text('Phone, email or both.', style: TextStyle(fontSize: 12, color: SokoColors.inkMuted)),
      const SizedBox(height: 8),
      TextField(
        controller: _receiptPhone,
        keyboardType: TextInputType.phone,
        decoration: InputDecoration(
          labelText: "Customer's phone (SMS)",
          hintText: '0244058519',
          suffixIcon: isPhone
              ? TextButton(
                  onPressed: () => setState(() => _receiptPhone.text = _account.text.trim()),
                  child: const Text('Same'))
              : null,
        ),
      ),
      const SizedBox(height: 8),
      TextField(
        controller: _receiptEmail,
        keyboardType: TextInputType.emailAddress,
        decoration: const InputDecoration(labelText: "Customer's email"),
      ),
      if (_error != null) ...[
        const SizedBox(height: 12),
        Text(_error!, style: const TextStyle(color: SokoColors.danger)),
      ],
      const SizedBox(height: 24),
      ElevatedButton(
        onPressed: _busy ? null : _submit,
        child: _busy
            ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
            : const Text('Pay from float'),
      ),
    ]);
  }

  Widget _receipt(SaleResult r) {
    Widget row(String k, String v, {bool copy = false}) => Padding(
          padding: const EdgeInsets.symmetric(vertical: 6),
          child: Row(children: [
            Expanded(child: Text(k, style: const TextStyle(color: SokoColors.inkMuted))),
            GestureDetector(
              onTap: copy
                  ? () {
                      Clipboard.setData(ClipboardData(text: v));
                      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Copied')));
                    }
                  : null,
              child: Text(v, style: TextStyle(fontWeight: FontWeight.w600, fontFamily: copy ? 'monospace' : null)),
            ),
          ]),
        );
    return ListView(padding: const EdgeInsets.all(20), children: [
      const Icon(Icons.check_circle, color: SokoColors.success, size: 80),
      const SizedBox(height: 12),
      const Text('Paid', textAlign: TextAlign.center, style: TextStyle(fontSize: 24, fontWeight: FontWeight.w700)),
      const SizedBox(height: 4),
      Text('Receipt sent to ${[_receiptPhone.text.trim(), _receiptEmail.text.trim()].where((s) => s.isNotEmpty).join(' and ')}.',
          textAlign: TextAlign.center),
      const SizedBox(height: 24),
      if (r.token.isNotEmpty) ...[
        Container(
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(color: SokoColors.surface, borderRadius: BorderRadius.circular(12)),
          child: Column(children: [
            const Text('Token — give this to the customer', style: TextStyle(color: SokoColors.inkMuted)),
            const SizedBox(height: 6),
            SelectableText(r.token, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w700, fontFamily: 'monospace')),
          ]),
        ),
        const SizedBox(height: 16),
      ],
      row('Taken from float', r.total),
      if (r.commission.isNotEmpty) row('You earned', r.commission),
      row('Float now', r.floatNow),
      row('Reference', r.reference, copy: true),
      const SizedBox(height: 24),
      ElevatedButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Done')),
    ]);
  }
}

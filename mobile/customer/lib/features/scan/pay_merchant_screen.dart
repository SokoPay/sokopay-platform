import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:sokopay_shared/sokopay_shared.dart';

/// Confirm and pay a merchant from the wallet. A fixed-amount request locks the
/// amount; a static QR lets the customer enter it.
class PayMerchantScreen extends StatefulWidget {
  const PayMerchantScreen({super.key, required this.target, required this.code});
  final Map<String, dynamic> target; // from GET /pay/resolve
  final String code;                 // what was scanned / typed

  @override
  State<PayMerchantScreen> createState() => _PayMerchantScreenState();
}

class _PayMerchantScreenState extends State<PayMerchantScreen> {
  final _amount = TextEditingController();
  bool _busy = false;
  String? _error;
  Map<String, dynamic>? _done;

  bool get _fixed => widget.target['amount'] != null;
  bool get _usable => widget.target['status'] == 'open';

  @override
  void initState() {
    super.initState();
    if (_fixed) _amount.text = widget.target['amount'].toString();
  }

  Future<void> _pay() async {
    setState(() { _busy = true; _error = null; });
    try {
      final r = await context.read<ApiClient>().post('/wallet/pay-merchant',
          data: {
            'code': widget.target['request_token'] ?? widget.target['merchant_code'] ?? widget.code,
            if (!_fixed) 'amount': _amount.text.trim(),
          },
          headers: {'Idempotency-Key': 'qr-${DateTime.now().microsecondsSinceEpoch}'});
      setState(() => _done = Map<String, dynamic>.from(r.data));
    } catch (e) {
      setState(() => _error = apiErrorMessage(e, fallback: 'Payment failed.'));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = widget.target;
    return Scaffold(
      appBar: AppBar(title: Text(t['merchant_name'] ?? 'Pay')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: _done != null ? _receipt(_done!) : _form(t),
      ),
    );
  }

  Widget _form(Map<String, dynamic> t) {
    return ListView(
      children: [
        Card(
          child: ListTile(
            leading: const Icon(Icons.storefront, color: SokoColors.orange),
            title: Text(t['merchant_name'] ?? '', style: const TextStyle(fontWeight: FontWeight.w600)),
            subtitle: Text((t['description'] as String?)?.isNotEmpty == true
                ? t['description']
                : 'Code ${t['merchant_code']}'),
          ),
        ),
        const SizedBox(height: 16),
        if (!_usable)
          Text(t['status'] == 'paid'
              ? 'This payment request has already been paid.'
              : 'This payment request has expired. Ask the shop for a new one.',
              style: const TextStyle(color: SokoColors.warning))
        else
          TextField(
            controller: _amount,
            readOnly: _fixed,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            style: const TextStyle(fontSize: 28, fontWeight: FontWeight.w600),
            decoration: InputDecoration(
              labelText: _fixed ? 'Amount (set by the shop)' : 'Amount (GH₵)',
              prefixText: 'GH₵ ',
            ),
          ),
        const SizedBox(height: 8),
        const Text('Paid from your SokoPay wallet.', style: TextStyle(color: Colors.black54)),
        if (_error != null) ...[
          const SizedBox(height: 12),
          Text(_error!, style: const TextStyle(color: SokoColors.danger)),
        ],
        const SizedBox(height: 24),
        ElevatedButton(
          onPressed: (_busy || !_usable) ? null : _pay,
          child: _busy
              ? const SizedBox(height: 20, width: 20,
                  child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
              : Text(_fixed ? 'Pay GH₵ ${t['amount']}' : 'Pay'),
        ),
      ],
    );
  }

  Widget _receipt(Map<String, dynamic> d) => Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            const Icon(Icons.check_circle, color: Colors.green, size: 64),
            const SizedBox(height: 16),
            const Text('Payment successful',
                style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            Text('GH₵ ${d['amount']} to ${d['merchant']}', style: const TextStyle(fontSize: 18)),
            const SizedBox(height: 4),
            Text('Ref ${d['reference']}', style: const TextStyle(color: Colors.black45)),
            const SizedBox(height: 24),
            ElevatedButton(
              onPressed: () => Navigator.of(context).pop(true),
              child: const Text('Done'),
            ),
          ],
        ),
      );
}

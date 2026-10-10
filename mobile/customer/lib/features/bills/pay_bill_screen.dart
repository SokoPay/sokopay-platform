import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:sokopay_shared/sokopay_shared.dart';

import '../common/pay_source_picker.dart';
import '../common/payment_tracker.dart';
import 'bills_service.dart';

/// Pay a bill or buy airtime:
///   account → (look up the account holder's name) → amount → pay with wallet or MoMo.
class PayBillScreen extends StatefulWidget {
  const PayBillScreen({super.key, required this.biller, required this.service});
  final Biller biller;
  final BillsService service;
  @override
  State<PayBillScreen> createState() => _PayBillScreenState();
}

class _PayBillScreenState extends State<PayBillScreen> {
  final _account = TextEditingController();
  final _amount = TextEditingController();
  String _source = 'mtn';
  bool _busy = false;
  String? _error;
  AccountLookupResult? _lookup;
  PaymentResult? _result;

  bool get _isAirtime => widget.biller.category == 'airtime';

  @override
  void initState() {
    super.initState();
    if (_isAirtime) _account.text = '+233';
  }

  Future<void> _checkAccount() async {
    setState(() { _busy = true; _error = null; _lookup = null; });
    try {
      final r = await widget.service.lookup(widget.biller.code, _account.text.trim());
      setState(() => _lookup = r);
    } catch (e) {
      setState(() => _error = apiErrorMessage(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  final _attempt = PaymentAttempt('bill');

  Future<void> _pay() async {
    final payer = context.read<AuthController>().me?['phone'] as String?;
    final wallet = PaySourcePicker.isWallet(_source);
    String? pin;
    if (wallet) {
      pin = await askPinFor(context, action: 'pay GH₵ ${_amount.text.trim()} to ${widget.biller.name}');
      if (pin == null || !mounted) return;
    }
    setState(() { _busy = true; _error = null; });
    final key = _attempt.key;
    try {
      final res = _isAirtime
          ? await widget.service.buyAirtime(
              billerCode: widget.biller.code,
              phone: _account.text.trim(),
              amount: _amount.text.trim(),
              source: wallet ? 'wallet' : 'momo',
              network: wallet ? null : _source,
              payer: wallet ? null : payer,
              idempotencyKey: key,
              pin: pin,
            )
          : await widget.service.payBill(
              billerCode: widget.biller.code,
              account: _account.text.trim(),
              amount: _amount.text.trim(),
              source: wallet ? 'wallet' : 'momo',
              network: wallet ? null : _source,
              payer: wallet ? null : payer,
              idempotencyKey: key,
              pin: pin,
            );
      _attempt.settle();
      setState(() => _result = res);
    } catch (e) {
      _attempt.settle(e);
      setState(() => _error = apiErrorMessage(e, fallback: 'Payment failed.'));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.biller.name)),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: _result != null
            ? PaymentTracker(initial: _result!, service: widget.service)
            : _form(),
      ),
    );
  }

  Widget _form() {
    final lookup = _lookup;
    return ListView(
      children: [
        TextField(
          controller: _account,
          keyboardType: _isAirtime ? TextInputType.phone : TextInputType.text,
          // Airtime numbers are E.164 (+233…); start the field with the prefix.
          decoration: InputDecoration(
            labelText: _isAirtime ? 'Phone number to top up' : 'Account / meter number',
            suffixIcon: widget.biller.supportsLookup
                ? TextButton(onPressed: _busy ? null : _checkAccount, child: const Text('Check'))
                : null,
          ),
          onChanged: (_) => setState(() => _lookup = null),
        ),
        if (lookup != null) ...[
          const SizedBox(height: 8),
          if (lookup.found)
            Row(children: [
              const Icon(Icons.verified_user, color: SokoColors.success, size: 18),
              const SizedBox(width: 6),
              Expanded(child: Text(lookup.accountName,
                  style: const TextStyle(fontWeight: FontWeight.w600))),
            ])
          else
            Text(lookup.supported ? 'Account not found. Check the number.' : lookup.message,
                style: const TextStyle(color: SokoColors.warning)),
        ],
        const SizedBox(height: 12),
        TextField(
          controller: _amount,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration: const InputDecoration(labelText: 'Amount (GH₵)', prefixText: 'GH₵ '),
        ),
        const SizedBox(height: 12),
        PaySourcePicker(value: _source, onChanged: (v) => setState(() => _source = v)),
        if (_error != null) ...[
          const SizedBox(height: 12),
          Text(_error!, style: const TextStyle(color: SokoColors.danger)),
        ],
        const SizedBox(height: 24),
        ElevatedButton(
          // Block paying an account we positively know doesn't exist.
          onPressed: _busy || (lookup != null && lookup.supported && !lookup.found)
              ? null
              : _pay,
          child: _busy
              ? const SizedBox(height: 20, width: 20,
                  child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
              : const Text('Pay'),
        ),
      ],
    );
  }
}

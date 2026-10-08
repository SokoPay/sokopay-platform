import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import 'agent_service.dart';

/// One screen for both cash operations, selected by [isCashIn].
///
///   Cash in  — the customer gives their phone number or SokoPay wallet ID; the agent
///              checks the NAME with them, then credits the wallet from float.
///   Cash out — the customer first presses "Cash out" in their own app; the agent then
///              raises a request; the customer approves it with their PIN. The agent
///              hands over cash ONLY when this screen says "approved".
class CashOpScreen extends StatefulWidget {
  const CashOpScreen({super.key, required this.isCashIn});
  final bool isCashIn;
  @override
  State<CashOpScreen> createState() => _CashOpScreenState();
}

class _CashOpScreenState extends State<CashOpScreen> {
  final _account = TextEditingController();
  final _amount = TextEditingController();
  late final AgentService _service;
  CustomerMatch? _match;
  bool _busy = false;
  String? _error;
  bool _cashInDone = false;
  CashOutReq? _request; // cash-out in progress
  Timer? _poll;
  Timer? _tick;

  @override
  void initState() {
    super.initState();
    _service = AgentService(context.read<ApiClient>());
  }

  @override
  void dispose() {
    _poll?.cancel();
    _tick?.cancel();
    _account.dispose();
    _amount.dispose();
    super.dispose();
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await action();
    } catch (e) {
      if (mounted) setState(() => _error = apiErrorMessage(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _checkName() => _run(() async {
        final m = await _service.lookupCustomer(_account.text.trim());
        setState(() => _match = m);
      });

  Future<void> _submit() => _run(() async {
        final account = _account.text.trim();
        final amount = _amount.text.trim();
        if (widget.isCashIn) {
          await _service.cashIn(account, amount);
          setState(() => _cashInDone = true);
        } else {
          final req = await _service.requestCashOut(account, amount);
          setState(() => _request = req);
          _poll = Timer.periodic(const Duration(seconds: 3), (_) => _refresh());
          _tick = Timer.periodic(const Duration(seconds: 1), (_) {
            if (mounted) setState(() {});
          });
        }
      });

  Future<void> _refresh() async {
    final current = _request;
    if (current == null) return;
    try {
      final next = await _service.cashOutStatus(current.id);
      if (!mounted) return;
      setState(() => _request = next);
      if (next.status != 'pending') {
        _poll?.cancel();
        _tick?.cancel();
      }
    } catch (_) {/* keep polling */}
  }

  @override
  Widget build(BuildContext context) {
    final title = widget.isCashIn ? 'Cash in' : 'Cash out';
    Widget body;
    if (_cashInDone) {
      body = _Outcome(
        icon: Icons.check_circle,
        color: SokoColors.success,
        title: 'Cash in successful',
        message: "${_amount.text} credited to ${_match?.name ?? 'the customer'}'s wallet.",
      );
    } else if (_request != null) {
      body = _cashOutProgress(_request!);
    } else {
      body = _form();
    }
    return Scaffold(appBar: AppBar(title: Text(title)), body: Padding(padding: const EdgeInsets.all(16), child: body));
  }

  Widget _form() {
    return ListView(children: [
      if (!widget.isCashIn)
        Container(
          padding: const EdgeInsets.all(12),
          margin: const EdgeInsets.only(bottom: 16),
          decoration: BoxDecoration(color: const Color(0xFFFFF3E0), borderRadius: BorderRadius.circular(8)),
          child: const Text(
            'First ask the customer to open SokoPay and press "Cash out". '
            'They will approve your request with their PIN. Never hand over cash before it says approved.',
            style: TextStyle(color: SokoColors.warning),
          ),
        ),
      TextField(
        controller: _account,
        keyboardType: TextInputType.number,
        decoration: InputDecoration(
          labelText: "Customer's phone or wallet ID",
          hintText: '0244058519 or 7XXX XXX XXX',
          suffixIcon: TextButton(onPressed: _busy ? null : _checkName, child: const Text('Check')),
        ),
        onChanged: (_) => setState(() => _match = null), // re-check after any edit
      ),
      if (_match != null)
        Card(
          margin: const EdgeInsets.only(top: 12),
          child: ListTile(
            leading: const Icon(Icons.verified_user, color: SokoColors.success),
            title: Text(_match!.name, style: const TextStyle(fontWeight: FontWeight.w700)),
            subtitle: Text('${_match!.phone}\nRead this name to the customer before you continue.'),
            isThreeLine: true,
          ),
        ),
      const SizedBox(height: 12),
      TextField(
        controller: _amount,
        enabled: _match != null,
        keyboardType: const TextInputType.numberWithOptions(decimal: true),
        inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'^\d*\.?\d{0,2}'))],
        decoration: const InputDecoration(labelText: 'Amount (GH₵)', prefixText: 'GH₵ '),
      ),
      if (_error != null) ...[
        const SizedBox(height: 12),
        Text(_error!, style: const TextStyle(color: SokoColors.danger)),
      ],
      const SizedBox(height: 24),
      ElevatedButton(
        onPressed: _busy || _match == null ? null : _submit,
        child: _busy
            ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
            : Text(widget.isCashIn ? 'Credit customer' : 'Send cash-out request'),
      ),
    ]);
  }

  Widget _cashOutProgress(CashOutReq r) {
    switch (r.status) {
      case 'approved':
        return _Outcome(
          icon: Icons.check_circle,
          color: SokoColors.success,
          title: 'Approved — hand over ${r.amountDisplay}',
          message: '${_match?.name ?? 'The customer'} approved with their PIN. Your float has been credited.',
        );
      case 'pending':
        final left = r.expiresAt.difference(DateTime.now());
        final mmss = left.isNegative
            ? '0:00'
            : '${left.inMinutes}:${(left.inSeconds % 60).toString().padLeft(2, '0')}';
        return _Outcome(
          icon: Icons.hourglass_top,
          color: SokoColors.warning,
          title: 'Waiting for the customer',
          message: '${_match?.name ?? 'The customer'} must approve ${r.amountDisplay} with their PIN in the '
              'SokoPay app.\nDo not hand over cash yet.\n\nExpires in $mmss',
          showDone: false,
        );
      default:
        final why = switch (r.status) {
          'declined' => 'The customer declined.',
          'expired' => 'The customer did not approve in time.',
          _ => r.reason.isNotEmpty ? r.reason : "It couldn't be completed.",
        };
        return _Outcome(
          icon: Icons.cancel,
          color: SokoColors.danger,
          title: 'Do NOT hand over cash',
          message: why,
        );
    }
  }
}

class _Outcome extends StatelessWidget {
  const _Outcome({
    required this.icon,
    required this.color,
    required this.title,
    required this.message,
    this.showDone = true,
  });
  final IconData icon;
  final Color color;
  final String title;
  final String message;
  final bool showDone;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Icon(icon, color: color, size: 80),
        const SizedBox(height: 16),
        Text(title, textAlign: TextAlign.center, style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700, color: color)),
        const SizedBox(height: 12),
        Text(message, textAlign: TextAlign.center, style: const TextStyle(fontSize: 16)),
        if (showDone) ...[
          const SizedBox(height: 32),
          ElevatedButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Done')),
        ],
      ]),
    );
  }
}

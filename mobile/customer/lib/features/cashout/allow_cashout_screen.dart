import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import '../wallet/wallet_service.dart';

/// "Cash out" at an agent — three steps, so an agent can never take money on their own:
///   1. Customer (at the agent) presses Cash out → a 10-minute window opens.
///   2. Agent enters the customer's phone / wallet ID and the amount → a request.
///   3. Customer sees the request here (agent name + amount) and approves with their PIN.
/// The screen polls every 3 seconds while open; a push also brings the customer here.
class AllowCashOutScreen extends StatefulWidget {
  const AllowCashOutScreen({super.key});
  @override
  State<AllowCashOutScreen> createState() => _AllowCashOutScreenState();
}

class _AllowCashOutScreenState extends State<AllowCashOutScreen> {
  late final ApiClient _api;
  Timer? _poll;
  Timer? _tick;
  DateTime? _windowEnds;
  List<Map<String, dynamic>> _requests = [];
  String? _walletNumber;
  String? _done; // success message after approval
  String? _error;
  bool _busy = false;
  bool _loaded = false;

  @override
  void initState() {
    super.initState();
    _api = context.read<ApiClient>();
    WalletService(_api).load().then((w) {
      if (mounted) setState(() => _walletNumber = w.walletNumber);
    }).catchError((_) {});
    _load();
    _poll = Timer.periodic(const Duration(seconds: 3), (_) => _load());
    _tick = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _poll?.cancel();
    _tick?.cancel();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final r = await _api.get('/wallet/cash-out-requests');
      final data = Map<String, dynamic>.from(r.data);
      if (!mounted) return;
      setState(() {
        _loaded = true;
        _windowEnds = data['window'] == null
            ? null
            : DateTime.parse(data['window']['expires_at']).toLocal();
        _requests = (data['requests'] as List).map((e) => Map<String, dynamic>.from(e)).toList();
      });
    } catch (e) {
      if (mounted) setState(() => _error = apiErrorMessage(e));
    }
  }

  Future<void> _act(Future<void> Function() action) async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await action();
      await _load();
    } catch (e) {
      if (mounted) setState(() => _error = apiErrorMessage(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _openWindow() => _act(() => _api.post('/wallet/cash-out/allow'));
  Future<void> _closeWindow() => _act(() => _api.delete('/wallet/cash-out/allow'));

  Future<void> _approve(Map<String, dynamic> req) async {
    final fee = (req['fee_minor'] ?? 0) > 0 ? ' (fee ${req['fee_display']})' : '';
    final pin = await _askPin(context, '${req['amount_display']}$fee from ${req['agent_name']}');
    if (pin == null) return;
    await _act(() async {
      await _api.post('/wallet/cash-out-requests/${req['id']}/approve', data: {'pin': pin});
      setState(() => _done = 'Approved. Collect ${req['amount_display']} from ${req['agent_name']}.');
    });
  }

  Future<void> _decline(Map<String, dynamic> req) =>
      _act(() => _api.post('/wallet/cash-out-requests/${req['id']}/decline'));

  String _left(DateTime until) {
    final d = until.difference(DateTime.now());
    if (d.isNegative) return '0:00';
    return '${d.inMinutes}:${(d.inSeconds % 60).toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Cash out')),
      body: Padding(padding: const EdgeInsets.all(20), child: _body()),
    );
  }

  Widget _body() {
    if (_done != null) {
      return _Centered(children: [
        const Icon(Icons.check_circle, color: SokoColors.success, size: 80),
        const SizedBox(height: 16),
        Text(_done!, textAlign: TextAlign.center, style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
        const SizedBox(height: 24),
        ElevatedButton(onPressed: () => Navigator.of(context).pop(), child: const Text('Done')),
      ]);
    }
    if (!_loaded) return const Center(child: CircularProgressIndicator());

    final children = <Widget>[];
    if (_requests.isNotEmpty) {
      children.add(const Text('Approve this cash-out?', style: TextStyle(fontSize: 20, fontWeight: FontWeight.w700)));
      children.add(const SizedBox(height: 4));
      children.add(const Text('Only approve if you are standing at this agent right now.',
          style: TextStyle(color: SokoColors.inkMuted)));
      for (final r in _requests) {
        children.add(Card(
          margin: const EdgeInsets.only(top: 16),
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Text(r['amount_display'] ?? '', style: const TextStyle(fontSize: 30, fontWeight: FontWeight.w800)),
              if ((r['fee_minor'] ?? 0) > 0)
                Text('Fee ${r['fee_display']} · ${r['total_display']} leaves your wallet',
                    style: const TextStyle(color: SokoColors.inkMuted)),
              const SizedBox(height: 4),
              Text('${r['agent_name']}${(r['agent_location'] ?? '').toString().isNotEmpty ? ' · ${r['agent_location']}' : ''}',
                  style: const TextStyle(fontSize: 16)),
              Text('Expires in ${_left(DateTime.parse(r['expires_at']).toLocal())}',
                  style: const TextStyle(color: SokoColors.inkMuted)),
              const SizedBox(height: 16),
              Row(children: [
                Expanded(
                  child: OutlinedButton(onPressed: _busy ? null : () => _decline(r), child: const Text('Decline')),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: ElevatedButton(onPressed: _busy ? null : () => _approve(r), child: const Text('Approve')),
                ),
              ]),
            ]),
          ),
        ));
      }
    } else if (_windowEnds != null) {
      children.addAll([
        const Icon(Icons.storefront, size: 64, color: SokoColors.orange),
        const SizedBox(height: 12),
        const Text('Cash out is on', textAlign: TextAlign.center,
            style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
        const SizedBox(height: 8),
        const Text('Give the agent your phone number or wallet ID and the amount. '
            'Their request will appear here for you to approve with your PIN.',
            textAlign: TextAlign.center, style: TextStyle(color: SokoColors.inkMuted)),
        if (_walletNumber != null && _walletNumber!.isNotEmpty) ...[
          const SizedBox(height: 16),
          GestureDetector(
            onTap: () => Clipboard.setData(ClipboardData(text: _walletNumber!.replaceAll(' ', ''))),
            child: Text('Wallet ID  $_walletNumber', textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w700, letterSpacing: 1.5)),
          ),
        ],
        const SizedBox(height: 16),
        const Row(mainAxisAlignment: MainAxisAlignment.center, children: [
          SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)),
          SizedBox(width: 10),
          Text('Waiting for the agent…'),
        ]),
        const SizedBox(height: 6),
        Text('Closes in ${_left(_windowEnds!)}', textAlign: TextAlign.center,
            style: const TextStyle(color: SokoColors.inkMuted)),
        const SizedBox(height: 24),
        OutlinedButton(onPressed: _busy ? null : _closeWindow, child: const Text('Cancel')),
      ]);
    } else {
      children.addAll([
        const Icon(Icons.local_atm, size: 72, color: SokoColors.orange),
        const SizedBox(height: 12),
        const Text('Get cash at a SokoPay agent', textAlign: TextAlign.center,
            style: TextStyle(fontSize: 22, fontWeight: FontWeight.w700)),
        const SizedBox(height: 12),
        const Text('1. Go to a SokoPay agent.\n'
            '2. Press "Cash out" below — this lets the agent send you a request for 10 minutes.\n'
            '3. Approve the agent\'s request here with your PIN, then collect your cash.',
            style: TextStyle(height: 1.5)),
        const SizedBox(height: 8),
        const Text('No agent can take money from your wallet without your PIN.',
            style: TextStyle(color: SokoColors.success, fontWeight: FontWeight.w600)),
        const SizedBox(height: 24),
        ElevatedButton(onPressed: _busy ? null : _openWindow, child: const Text("I'm at an agent — Cash out")),
      ]);
    }
    if (_error != null) {
      children.add(Padding(
        padding: const EdgeInsets.only(top: 16),
        child: Text(_error!, textAlign: TextAlign.center, style: const TextStyle(color: SokoColors.danger)),
      ));
    }
    return ListView(children: children);
  }
}

class _Centered extends StatelessWidget {
  const _Centered({required this.children});
  final List<Widget> children;
  @override
  Widget build(BuildContext context) =>
      Center(child: Column(mainAxisSize: MainAxisSize.min, children: children));
}

/// PIN sheet for approving; the PIN is checked by the server (shared lockout).
Future<String?> _askPin(BuildContext context, String what) {
  final controller = TextEditingController();
  return showModalBottomSheet<String>(
    context: context,
    isScrollControlled: true,
    builder: (c) => Padding(
      padding: EdgeInsets.fromLTRB(24, 24, 24, 24 + MediaQuery.of(c).viewInsets.bottom),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        const Text('Enter your PIN', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
        Text('To approve cash-out of $what', style: const TextStyle(color: SokoColors.inkMuted)),
        const SizedBox(height: 16),
        TextField(
          controller: controller,
          autofocus: true,
          obscureText: true,
          maxLength: 6,
          keyboardType: TextInputType.number,
          textAlign: TextAlign.center,
          inputFormatters: [FilteringTextInputFormatter.digitsOnly],
          style: const TextStyle(fontSize: 24, letterSpacing: 12),
          decoration: const InputDecoration(counterText: ''),
          onChanged: (v) {
            if (v.length == 6) Navigator.pop(c, v);
          },
        ),
        TextButton(onPressed: () => Navigator.pop(c), child: const Text('Cancel')),
      ]),
    ),
  );
}

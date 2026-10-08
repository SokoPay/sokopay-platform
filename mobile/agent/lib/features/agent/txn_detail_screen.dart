import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import 'agent_service.dart';
import 'history_screen.dart';

/// One transaction — what to show a customer who disputes a cash-in/out, or to quote
/// (the AG- reference) when calling support. The customer's number is masked by the
/// server; the agent can still match it against the customer's phone.
class TxnDetailScreen extends StatefulWidget {
  const TxnDetailScreen({super.key, required this.id});
  final String id;
  @override
  State<TxnDetailScreen> createState() => _TxnDetailScreenState();
}

class _TxnDetailScreenState extends State<TxnDetailScreen> {
  late Future<HistoryTxn> _txn;

  @override
  void initState() {
    super.initState();
    _txn = AgentService(context.read<ApiClient>()).transaction(widget.id);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Transaction')),
      body: FutureBuilder<HistoryTxn>(
        future: _txn,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) {
            return Center(child: Padding(padding: const EdgeInsets.all(24), child: Text(apiErrorMessage(snap.error!))));
          }
          final t = snap.data!;
          final (icon, color) = kindStyle(t.kind);
          return ListView(padding: const EdgeInsets.all(20), children: [
            Center(
              child: CircleAvatar(radius: 32, backgroundColor: color.withValues(alpha: 0.12),
                  child: Icon(icon, color: color, size: 32)),
            ),
            const SizedBox(height: 12),
            Text(t.amountDisplay,
                textAlign: TextAlign.center, style: const TextStyle(fontSize: 32, fontWeight: FontWeight.w700)),
            Text(t.kindDisplay,
                textAlign: TextAlign.center, style: TextStyle(color: color, fontWeight: FontWeight.w600)),
            const SizedBox(height: 24),
            if (t.customer.isNotEmpty) _Row('Customer', t.customer),
            _Row('Effect on your float', t.floatEffect),
            _Row('When', DateFormat('d MMM yyyy, HH:mm').format(t.createdAt)),
            const Divider(height: 32),
            _Row('Reference', t.reference, copyable: true),
            const SizedBox(height: 8),
            const Text('Quote this reference if you contact SokoPay support about this transaction.',
                style: TextStyle(color: SokoColors.inkMuted, fontSize: 12)),
          ]);
        },
      ),
    );
  }
}

class _Row extends StatelessWidget {
  const _Row(this.label, this.value, {this.copyable = false});
  final String label;
  final String value;
  final bool copyable;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(children: [
        Expanded(child: Text(label, style: const TextStyle(color: SokoColors.inkMuted))),
        GestureDetector(
          onTap: copyable
              ? () {
                  Clipboard.setData(ClipboardData(text: value));
                  ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Copied')));
                }
              : null,
          child: Text(value,
              style: TextStyle(fontWeight: FontWeight.w600, fontFamily: copyable ? 'monospace' : null)),
        ),
      ]),
    );
  }
}

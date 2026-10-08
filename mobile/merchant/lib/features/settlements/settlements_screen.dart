import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import '../business/merchant_service.dart';
import 'pin_confirm.dart';

/// Settlements: what's available, where it's paid to, request a payout (PIN-confirmed),
/// and the history with live status (awaiting approval / processing / paid / failed).
class SettlementsScreen extends StatefulWidget {
  const SettlementsScreen({super.key});
  @override
  State<SettlementsScreen> createState() => _SettlementsScreenState();
}

class _SettlementsScreenState extends State<SettlementsScreen> {
  late final MerchantService _service;
  late Future<SettlementOverview> _data;

  @override
  void initState() {
    super.initState();
    _service = MerchantService(context.read<ApiClient>());
    _data = _service.settlements();
  }

  Future<void> _refresh() async {
    final next = _service.settlements();
    setState(() => _data = next);
    await next.catchError((_) => SettlementOverview.fromJson(const {}));
  }

  Future<void> _requestSettlement(SettlementOverview d) async {
    final usable = d.accounts.where((a) => a.verified).toList();
    if (usable.isEmpty) {
      _snack(d.accounts.isEmpty
          ? 'Add a settlement account first.'
          : 'Your settlement account is being verified by SokoPay. You can settle once it is.');
      return;
    }
    final choice = await showModalBottomSheet<(String?, SettlementAccountInfo)>(
      context: context,
      isScrollControlled: true,
      builder: (_) => _RequestSheet(overview: d, accounts: usable),
    );
    if (choice == null || !mounted) return;
    final pin = await askForPin(context, action: 'settle ${choice.$1 == null ? d.availableDisplay : 'GH₵ ${choice.$1}'}');
    if (pin == null) return;
    try {
      final s = await _service.requestSettlement(amount: choice.$1, accountId: choice.$2.id, pin: pin);
      _snack(s.needsApproval
          ? 'Submitted. SokoPay approves large amounts and new accounts before paying.'
          : 'Settlement on its way to ${choice.$2.label}.');
    } catch (e) {
      _snack(apiErrorMessage(e));
    }
    await _refresh();
  }

  void _snack(String text) {
    if (mounted) ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Settlements')),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: FutureBuilder<SettlementOverview>(
          future: _data,
          builder: (context, snap) {
            if (snap.connectionState != ConnectionState.done) {
              return const Center(child: CircularProgressIndicator());
            }
            if (snap.hasError) {
              return ListView(padding: const EdgeInsets.all(24), children: [
                Text(apiErrorMessage(snap.error!), textAlign: TextAlign.center),
              ]);
            }
            final d = snap.data!;
            return ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Container(
                  padding: const EdgeInsets.all(20),
                  decoration: BoxDecoration(color: SokoColors.ink, borderRadius: BorderRadius.circular(12)),
                  child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    const Text('Available to settle', style: TextStyle(color: Colors.white70)),
                    const SizedBox(height: 6),
                    Text(d.availableDisplay,
                        style: const TextStyle(color: Colors.white, fontSize: 30, fontWeight: FontWeight.w700)),
                    if (d.canMoveMoney) ...[
                      const SizedBox(height: 16),
                      ElevatedButton(
                        onPressed: d.availableMinor > 0 ? () => _requestSettlement(d) : null,
                        child: const Text('Settle now'),
                      ),
                    ],
                  ]),
                ),
                const SizedBox(height: 8),
                const Text('Settlements also run automatically each morning (T+1) to your default account.',
                    style: TextStyle(color: SokoColors.inkMuted, fontSize: 12)),
                const SizedBox(height: 24),
                Row(children: [
                  const Expanded(child: Text('Settlement accounts', style: TextStyle(fontWeight: FontWeight.w600))),
                  if (d.canMoveMoney)
                    TextButton.icon(
                      onPressed: () async {
                        await context.push('/settlements/add-account', extra: d.bankPayoutsAvailable);
                        _refresh();
                      },
                      icon: const Icon(Icons.add),
                      label: const Text('Add'),
                    ),
                ]),
                if (d.accounts.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 8),
                    child: Text('No accounts yet.', style: TextStyle(color: Colors.black45)),
                  ),
                ...d.accounts.map((a) => ListTile(
                      contentPadding: EdgeInsets.zero,
                      leading: Icon(a.kind == 'bank' ? Icons.account_balance : Icons.phone_android),
                      title: Text(a.label),
                      subtitle: Text(a.accountName + (a.isDefault ? ' · default' : '')),
                      trailing: a.verified
                          ? const Icon(Icons.verified, color: SokoColors.success)
                          : const Tooltip(
                              message: 'SokoPay is checking the name on this account',
                              child: Icon(Icons.hourglass_top, color: SokoColors.warning)),
                    )),
                const SizedBox(height: 24),
                const Text('History', style: TextStyle(fontWeight: FontWeight.w600)),
                if (d.settlements.isEmpty)
                  const Padding(
                    padding: EdgeInsets.symmetric(vertical: 8),
                    child: Text('No settlements yet.', style: TextStyle(color: Colors.black45)),
                  ),
                ...d.settlements.map((s) => ListTile(
                      contentPadding: EdgeInsets.zero,
                      title: Text(s.amountDisplay, style: const TextStyle(fontWeight: FontWeight.w600)),
                      subtitle: Text('${DateFormat('d MMM, HH:mm').format(s.createdAt)} · ${s.destination}'),
                      trailing: SettlementStatusChip(status: s.status, label: s.statusDisplay),
                    )),
              ],
            );
          },
        ),
      ),
    );
  }
}

/// Amount (blank = everything) + which verified account. Returns (amount?, account).
class _RequestSheet extends StatefulWidget {
  const _RequestSheet({required this.overview, required this.accounts});
  final SettlementOverview overview;
  final List<SettlementAccountInfo> accounts;
  @override
  State<_RequestSheet> createState() => _RequestSheetState();
}

class _RequestSheetState extends State<_RequestSheet> {
  final _amount = TextEditingController();
  late SettlementAccountInfo _account =
      widget.accounts.firstWhere((a) => a.isDefault, orElse: () => widget.accounts.first);

  @override
  void dispose() {
    _amount.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.fromLTRB(24, 24, 24, 24 + MediaQuery.of(context).viewInsets.bottom),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        const Text('Settle now', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
        const SizedBox(height: 16),
        TextField(
          controller: _amount,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'^\d*\.?\d{0,2}'))],
          decoration: InputDecoration(
            labelText: 'Amount (GH₵)',
            hintText: 'Blank = all of ${widget.overview.availableDisplay}',
            prefixText: 'GH₵ ',
          ),
        ),
        const SizedBox(height: 12),
        DropdownButtonFormField<SettlementAccountInfo>(
          initialValue: _account,
          isExpanded: true,
          decoration: const InputDecoration(labelText: 'Pay to'),
          items: widget.accounts
              .map((a) => DropdownMenuItem(value: a, child: Text(a.label, overflow: TextOverflow.ellipsis)))
              .toList(),
          onChanged: (a) => setState(() => _account = a ?? _account),
        ),
        const SizedBox(height: 20),
        ElevatedButton(
          onPressed: () {
            final t = _amount.text.trim();
            Navigator.pop(context, (t.isEmpty ? null : t, _account));
          },
          child: const Text('Continue'),
        ),
      ]),
    );
  }
}

class SettlementStatusChip extends StatelessWidget {
  const SettlementStatusChip({super.key, required this.status, required this.label});
  final String status;
  final String label;

  @override
  Widget build(BuildContext context) {
    final color = switch (status) {
      'paid' => SokoColors.success,
      'failed' || 'rejected' => SokoColors.danger,
      'processing' => Colors.blue,
      _ => SokoColors.warning,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(color: color.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(20)),
      child: Text(label, style: TextStyle(color: color, fontWeight: FontWeight.w600, fontSize: 12)),
    );
  }
}

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';
import 'package:intl/intl.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

import 'agent_service.dart';

/// Buy float: pay cash into one of SokoPay's bank accounts, then send the slip details
/// (and a photo of the slip, if you can). SokoPay checks the money has arrived and adds
/// it to your float — you get a notification either way.
class DepositScreen extends StatefulWidget {
  const DepositScreen({super.key});
  @override
  State<DepositScreen> createState() => _DepositScreenState();
}

class _DepositScreenState extends State<DepositScreen> {
  late final AgentService _service;
  late Future<List<DepositAccount>> _accounts;
  late Future<List<FloatDeposit>> _deposits;

  @override
  void initState() {
    super.initState();
    _service = AgentService(context.read<ApiClient>());
    _refresh();
  }

  void _refresh() => setState(() {
        _accounts = _service.depositAccounts();
        _deposits = _service.deposits();
      });

  Future<void> _new(List<DepositAccount> accounts) async {
    final sent = await Navigator.of(context).push<bool>(MaterialPageRoute(
      builder: (_) => _DepositForm(service: _service, accounts: accounts),
    ));
    if (sent == true) _refresh();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Buy float')),
      body: RefreshIndicator(
        onRefresh: () async => _refresh(),
        child: ListView(padding: const EdgeInsets.all(16), children: [
          const Text('1. Pay cash into one of these SokoPay accounts at the bank.',
              style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          FutureBuilder<List<DepositAccount>>(
            future: _accounts,
            builder: (context, snap) {
              if (snap.connectionState != ConnectionState.done) {
                return const Padding(padding: EdgeInsets.all(16), child: Center(child: CircularProgressIndicator()));
              }
              if (snap.hasError) return Text(apiErrorMessage(snap.error!));
              final accounts = snap.data!;
              if (accounts.isEmpty) {
                return const Text('No SokoPay bank accounts are set up yet. Contact SokoPay to buy float.',
                    style: TextStyle(color: SokoColors.inkMuted));
              }
              return Column(children: [
                for (final a in accounts)
                  Card(
                    child: ListTile(
                      leading: const Icon(Icons.account_balance),
                      title: Text(a.bankName, style: const TextStyle(fontWeight: FontWeight.w600)),
                      subtitle: Text('${a.accountName}\n${a.accountNumber}${a.branch.isNotEmpty ? ' · ${a.branch}' : ''}'),
                      isThreeLine: true,
                      trailing: IconButton(
                        tooltip: 'Copy account number',
                        icon: const Icon(Icons.copy),
                        onPressed: () {
                          Clipboard.setData(ClipboardData(text: a.accountNumber));
                          ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('Copied')));
                        },
                      ),
                    ),
                  ),
                const SizedBox(height: 16),
                const Align(
                  alignment: Alignment.centerLeft,
                  child: Text('2. Send us the slip details.', style: TextStyle(fontWeight: FontWeight.w600)),
                ),
                const SizedBox(height: 8),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton.icon(
                    icon: const Icon(Icons.receipt_long),
                    label: const Text("I've made a deposit"),
                    onPressed: () => _new(accounts),
                  ),
                ),
              ]);
            },
          ),
          const SizedBox(height: 24),
          const Text('Your deposits', style: TextStyle(fontWeight: FontWeight.w600)),
          FutureBuilder<List<FloatDeposit>>(
            future: _deposits,
            builder: (context, snap) {
              final rows = snap.data ?? const <FloatDeposit>[];
              if (snap.connectionState == ConnectionState.done && rows.isEmpty) {
                return const Padding(
                    padding: EdgeInsets.all(16),
                    child: Text('None yet.', style: TextStyle(color: Colors.black45)));
              }
              return Column(children: [
                for (final d in rows)
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text('${d.amount} · ${d.bank}'),
                    subtitle: Text([
                      'Slip ${d.reference}',
                      if (d.depositDate != null) DateFormat('d MMM yyyy').format(d.depositDate!),
                      if (d.note.isNotEmpty) d.note,
                    ].join(' · ')),
                    trailing: _StatusChip(status: d.status, label: d.statusDisplay),
                  ),
              ]);
            },
          ),
        ]),
      ),
    );
  }
}

class _StatusChip extends StatelessWidget {
  const _StatusChip({required this.status, required this.label});
  final String status;
  final String label;
  @override
  Widget build(BuildContext context) {
    final color = switch (status) {
      'approved' => SokoColors.success,
      'rejected' => SokoColors.danger,
      _ => SokoColors.warning,
    };
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(color: color.withValues(alpha: 0.12), borderRadius: BorderRadius.circular(20)),
      child: Text(status == 'pending' ? 'Being checked' : label, style: TextStyle(color: color, fontSize: 12)),
    );
  }
}

class _DepositForm extends StatefulWidget {
  const _DepositForm({required this.service, required this.accounts});
  final AgentService service;
  final List<DepositAccount> accounts;
  @override
  State<_DepositForm> createState() => _DepositFormState();
}

class _DepositFormState extends State<_DepositForm> {
  DepositAccount? _account;
  final _amount = TextEditingController();
  final _reference = TextEditingController();
  DateTime _date = DateTime.now();
  Uint8List? _slip;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    if (widget.accounts.length == 1) _account = widget.accounts.first;
  }

  @override
  void dispose() {
    _amount.dispose();
    _reference.dispose();
    super.dispose();
  }

  Future<void> _pickSlip() async {
    final source = await showModalBottomSheet<ImageSource>(
      context: context,
      builder: (ctx) => SafeArea(
        child: Wrap(children: [
          ListTile(
              leading: const Icon(Icons.photo_camera),
              title: const Text('Take a photo'),
              onTap: () => Navigator.pop(ctx, ImageSource.camera)),
          ListTile(
              leading: const Icon(Icons.photo_library),
              title: const Text('Choose from gallery'),
              onTap: () => Navigator.pop(ctx, ImageSource.gallery)),
        ]),
      ),
    );
    if (source == null) return;
    final shot = await ImagePicker().pickImage(source: source, maxWidth: 1800, imageQuality: 85);
    if (shot == null) return;
    final bytes = await shot.readAsBytes();
    if (mounted) setState(() => _slip = bytes);
  }

  Future<void> _pickDate() async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: _date,
      firstDate: now.subtract(const Duration(days: 30)),
      lastDate: now,
      helpText: 'Date on the deposit slip',
    );
    if (picked != null) setState(() => _date = picked);
  }

  Future<void> _submit() async {
    String? problem;
    if (_account == null) {
      problem = 'Choose the bank account you paid into.';
    } else if ((double.tryParse(_amount.text.trim()) ?? 0) <= 0) {
      problem = 'Enter the amount you deposited.';
    } else if (_reference.text.trim().length < 4) {
      problem = 'Enter the reference on your deposit slip.';
    }
    setState(() => _error = problem);
    if (problem != null) return;
    final nav = Navigator.of(context);
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _busy = true);
    try {
      await widget.service.submitDeposit(
        accountId: _account!.id,
        amount: _amount.text.trim(),
        reference: _reference.text.trim(),
        depositDate: DateFormat('yyyy-MM-dd').format(_date),
        slipJpeg: _slip,
      );
      messenger.showSnackBar(const SnackBar(
          content: Text("Sent. We'll add it to your float once the money is confirmed.")));
      nav.pop(true);
    } catch (e) {
      if (mounted) setState(() => _error = apiErrorMessage(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Deposit details')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        DropdownButtonFormField<DepositAccount>(
          initialValue: _account,
          isExpanded: true,
          decoration: const InputDecoration(labelText: 'Paid into'),
          items: [
            for (final a in widget.accounts)
              DropdownMenuItem(value: a, child: Text('${a.bankName} · ${a.accountNumber}', overflow: TextOverflow.ellipsis)),
          ],
          onChanged: (a) => setState(() => _account = a),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _amount,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          inputFormatters: [FilteringTextInputFormatter.allow(RegExp(r'^\d*\.?\d{0,2}'))],
          decoration: const InputDecoration(labelText: 'Amount deposited (GH₵)', prefixText: 'GH₵ '),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _reference,
          decoration: const InputDecoration(labelText: 'Reference / transaction number on the slip'),
        ),
        const SizedBox(height: 12),
        ListTile(
          contentPadding: EdgeInsets.zero,
          leading: const Icon(Icons.event),
          title: const Text('Date on the slip'),
          subtitle: Text(DateFormat('d MMM yyyy').format(_date)),
          trailing: const Icon(Icons.edit_calendar_outlined),
          onTap: _pickDate,
        ),
        const SizedBox(height: 8),
        if (_slip != null)
          ClipRRect(borderRadius: BorderRadius.circular(8), child: Image.memory(_slip!, height: 180, fit: BoxFit.cover)),
        TextButton.icon(
          icon: const Icon(Icons.photo_camera_outlined),
          label: Text(_slip == null ? 'Add a photo of the slip (recommended)' : 'Change photo'),
          onPressed: _pickSlip,
        ),
        if (_error != null) ...[
          const SizedBox(height: 8),
          Text(_error!, style: const TextStyle(color: SokoColors.danger)),
        ],
        const SizedBox(height: 16),
        ElevatedButton(
          onPressed: _busy ? null : _submit,
          child: _busy
              ? const SizedBox(height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
              : const Text('Send for approval'),
        ),
      ]),
    );
  }
}

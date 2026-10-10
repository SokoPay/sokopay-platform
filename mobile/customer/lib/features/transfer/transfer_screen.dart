import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:sokopay_shared/sokopay_shared.dart';

import 'transfer_service.dart';
import '../common/saved_recipients.dart';

const _momo = {'mtn': 'MTN MoMo', 'telecel': 'Telecel Cash', 'at': 'AT Money'};
const _wallets = {'gmoney': 'G-Money', 'zeepay': 'Zeepay'};

/// Send from the SokoPay wallet to another MoMo wallet, a bank, or another fintech.
/// The recipient's registered name must be confirmed before money moves.
class TransferScreen extends StatefulWidget {
  const TransferScreen({super.key, this.initialType});

  /// 'momo' | 'bank' | 'wallet' — preselects the destination (from the Transfers hub).
  final String? initialType;
  @override
  State<TransferScreen> createState() => _TransferScreenState();
}

class _TransferScreenState extends State<TransferScreen> {
  late final TransferService _service;
  late String _type = const {'momo', 'bank', 'wallet'}.contains(widget.initialType)
      ? widget.initialType!
      : 'momo';
  late String _institution = _type == 'momo' ? 'mtn' : _type == 'wallet' ? 'gmoney' : '';
  final _account = TextEditingController(text: '+233');
  final _bankCode = TextEditingController();
  final _amount = TextEditingController();
  final _narrative = TextEditingController();
  RecipientCheck? _check;
  bool _busy = false;
  String? _error;
  TransferRecord? _done;

  @override
  void initState() {
    super.initState();
    _service = TransferService(context.read<ApiClient>());
  }

  TransferTarget get _target => TransferTarget(
        _type,
        _type == 'bank' ? _bankCode.text.trim() : _institution,
        _account.text.trim(),
      );

  void _setType(String type) => setState(() {
        _type = type;
        _check = null;
        _error = null;
        _institution = type == 'momo' ? 'mtn' : type == 'wallet' ? 'gmoney' : '';
        _account.text = type == 'bank' ? '' : '+233';
      });

  Future<void> _lookup() async {
    setState(() { _busy = true; _error = null; _check = null; });
    try {
      final c = await _service.lookup(_target);
      setState(() => _check = c);
    } catch (e) {
      setState(() => _error = apiErrorMessage(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  final _attempt = PaymentAttempt('xfer');

  Future<void> _send() async {
    final pin = await askPinFor(context, action: 'send GH₵ ${_amount.text.trim()} to ${_check?.name ?? 'this account'}');
    if (pin == null || !mounted) return;
    setState(() { _busy = true; _error = null; });
    try {
      final t = await _service.send(_target, _amount.text.trim(), _narrative.text.trim(), _attempt.key, pin: pin);
      _attempt.settle();
      setState(() => _done = t);
    } catch (e) {
      _attempt.settle(e);
      setState(() => _error = apiErrorMessage(e, fallback: 'Transfer failed.'));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Send money'),
        actions: [
          IconButton(
            tooltip: 'History',
            icon: const Icon(Icons.history),
            onPressed: () => Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const TransferHistoryScreen())),
          ),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: _done != null ? _result(_done!) : _form(),
      ),
    );
  }

  Widget _form() {
    final check = _check;
    final confirmed = check != null && check.found;
    return ListView(
      children: [
        SegmentedButton<String>(
          segments: const [
            ButtonSegment(value: 'momo', label: Text('Mobile money'), icon: Icon(Icons.phone_android)),
            ButtonSegment(value: 'bank', label: Text('Bank'), icon: Icon(Icons.account_balance)),
            ButtonSegment(value: 'wallet', label: Text('Other wallet'), icon: Icon(Icons.wallet)),
          ],
          selected: {_type},
          onSelectionChanged: (s) => _setType(s.first),
        ),
        const SizedBox(height: 16),
        if (_type == 'bank')
          TextField(
            controller: _bankCode,
            decoration: const InputDecoration(
              labelText: 'Bank code',
              helperText: 'The bank list will load from GhIPSS once bank transfers go live.',
            ),
            onChanged: (_) => setState(() => _check = null),
          )
        else
          DropdownButtonFormField<String>(
            initialValue: _institution,
            decoration: InputDecoration(labelText: _type == 'momo' ? 'Network' : 'Provider'),
            items: [
              for (final e in (_type == 'momo' ? _momo : _wallets).entries)
                DropdownMenuItem(value: e.key, child: Text(e.value)),
            ],
            onChanged: (v) => setState(() { _institution = v!; _check = null; }),
          ),
        const SizedBox(height: 12),
        TextField(
          controller: _account,
          keyboardType: _type == 'bank' ? TextInputType.number : TextInputType.phone,
          decoration: InputDecoration(
            labelText: _type == 'bank' ? 'Account number' : 'Phone number',
            prefixIcon: IconButton(
              tooltip: 'Saved recipients',
              icon: const Icon(Icons.star_border, color: SokoColors.orange),
              onPressed: () async {
                final r = await pickSavedRecipient(context, kind: _type);
                if (r == null || !mounted) return;
                setState(() {
                  _account.text = r['value'];
                  if (_type == 'bank') {
                    _bankCode.text = (r['institution'] ?? '').toString().toUpperCase();
                  } else if ((r['institution'] ?? '') != '') {
                    _institution = r['institution'];
                  }
                  _check = null;
                });
              },
            ),
            suffixIcon: TextButton(onPressed: _busy ? null : _lookup, child: const Text('Check name')),
          ),
          onChanged: (_) => setState(() => _check = null),
        ),
        if (check != null) ...[
          const SizedBox(height: 8),
          if (check.found)
            Row(children: [
              const Icon(Icons.verified_user, color: SokoColors.success, size: 18),
              const SizedBox(width: 6),
              Expanded(child: Text(check.name,
                  style: const TextStyle(fontWeight: FontWeight.w600))),
            ])
          else
            Text(check.supported ? 'Recipient not found. Check the details.' : check.message,
                style: const TextStyle(color: SokoColors.warning)),
        ],
        const SizedBox(height: 12),
        TextField(
          controller: _amount,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration: const InputDecoration(labelText: 'Amount (GH₵)', prefixText: 'GH₵ '),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _narrative,
          maxLength: 140,
          decoration: const InputDecoration(labelText: 'Note (optional)'),
        ),
        if (_error != null) ...[
          const SizedBox(height: 8),
          Text(_error!, style: const TextStyle(color: SokoColors.danger)),
        ],
        const SizedBox(height: 16),
        ElevatedButton(
          onPressed: (_busy || !confirmed) ? null : _send,
          child: _busy
              ? const SizedBox(height: 20, width: 20,
                  child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
              : Text(confirmed ? 'Send to ${check.name}' : 'Check the name first'),
        ),
      ],
    );
  }

  Widget _result(TransferRecord t) {
    final (icon, color, title) = switch (t.status) {
      'succeeded' => (Icons.check_circle, Colors.green, 'Sent'),
      'failed' => (Icons.cancel, Colors.red, 'Transfer failed — money returned to your wallet'),
      _ => (Icons.hourglass_top, Colors.orange, 'Processing'),
    };
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(icon, color: color, size: 64),
          const SizedBox(height: 16),
          Text(title, textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Text(t.amount, style: const TextStyle(fontSize: 24)),
          Text('to ${t.accountName.isEmpty ? t.account : t.accountName}'),
          const SizedBox(height: 4),
          Text('Ref ${t.reference}', style: const TextStyle(color: Colors.black45)),
          if (t.status == 'pending') ...[
            const SizedBox(height: 12),
            const Text('We’ll notify you when it arrives. If it fails, the money comes back to your wallet.',
                textAlign: TextAlign.center, style: TextStyle(color: Colors.black54)),
          ],
          const SizedBox(height: 12),
          SaveRecipientButton(kind: _type, value: _account.text.trim(),
              institution: _type == 'bank' ? _bankCode.text.trim() : _institution),
          const SizedBox(height: 12),
          ElevatedButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text('Done'),
          ),
        ],
      ),
    );
  }
}

class TransferHistoryScreen extends StatelessWidget {
  const TransferHistoryScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final service = TransferService(context.read<ApiClient>());
    return Scaffold(
      appBar: AppBar(title: const Text('Transfers')),
      body: FutureBuilder<List<TransferRecord>>(
        future: service.history(),
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const Center(child: CircularProgressIndicator());
          }
          if (snap.hasError) return Center(child: Text(apiErrorMessage(snap.error!)));
          final rows = snap.data!;
          if (rows.isEmpty) return const Center(child: Text('No transfers yet.'));
          return ListView.separated(
            itemCount: rows.length,
            separatorBuilder: (_, __) => const Divider(height: 1),
            itemBuilder: (_, i) {
              final t = rows[i];
              return ListTile(
                title: Text(t.accountName.isEmpty ? t.account : t.accountName),
                subtitle: Text('${t.institution.toUpperCase()} · ${t.reference}'),
                trailing: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text(t.amount, style: const TextStyle(fontWeight: FontWeight.w600)),
                    Text(t.status, style: TextStyle(
                        fontSize: 12,
                        color: t.status == 'succeeded' ? SokoColors.success
                            : t.status == 'failed' ? SokoColors.danger : SokoColors.warning)),
                  ],
                ),
              );
            },
          );
        },
      ),
    );
  }
}

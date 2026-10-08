import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'package:sokopay_shared/sokopay_shared.dart';

/// Pick a saved recipient of [kind] (sokopay | momo | bank | wallet | merchant).
/// Returns {label, value, institution} or null. Picking only fills the form: the
/// name check and the payment still run as usual.
Future<Map<String, dynamic>?> pickSavedRecipient(BuildContext context, {required String kind}) {
  return showModalBottomSheet<Map<String, dynamic>>(
    context: context,
    builder: (_) => _SavedSheet(kind: kind),
  );
}

class _SavedSheet extends StatefulWidget {
  const _SavedSheet({required this.kind});
  final String kind;
  @override
  State<_SavedSheet> createState() => _SavedSheetState();
}

class _SavedSheetState extends State<_SavedSheet> {
  late Future<List<Map<String, dynamic>>> _items;

  @override
  void initState() {
    super.initState();
    _items = context.read<ApiClient>().get('/wallet/saved', query: {'kind': widget.kind}).then((r) =>
        ((r.data as Map)['results'] as List).map((e) => Map<String, dynamic>.from(e)).toList());
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: FutureBuilder<List<Map<String, dynamic>>>(
        future: _items,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const SizedBox(height: 160, child: Center(child: CircularProgressIndicator()));
          }
          final items = snap.data ?? const [];
          return ListView(shrinkWrap: true, children: [
            const Padding(
              padding: EdgeInsets.all(16),
              child: Text('Saved recipients', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
            ),
            if (items.isEmpty)
              const Padding(
                padding: EdgeInsets.fromLTRB(16, 0, 16, 24),
                child: Text('Nobody saved yet. After a successful send, tap "Save recipient".',
                    style: TextStyle(color: SokoColors.inkMuted)),
              ),
            for (final r in items)
              ListTile(
                leading: const Icon(Icons.star, color: SokoColors.orange),
                title: Text(r['label'] ?? ''),
                subtitle: Text('${(r['institution'] ?? '').toString().toUpperCase()} ${r['value']}'.trim()),
                onTap: () => Navigator.pop(context, r),
              ),
          ]);
        },
      ),
    );
  }
}

/// "Save recipient" after a successful send. The server re-checks the account before
/// saving, so a mistyped number is never stored.
class SaveRecipientButton extends StatefulWidget {
  const SaveRecipientButton({super.key, required this.kind, required this.value, this.institution = ''});
  final String kind;
  final String value;
  final String institution;
  @override
  State<SaveRecipientButton> createState() => _SaveRecipientButtonState();
}

class _SaveRecipientButtonState extends State<SaveRecipientButton> {
  bool _saved = false;
  bool _busy = false;

  Future<void> _save() async {
    setState(() => _busy = true);
    final messenger = ScaffoldMessenger.of(context);
    try {
      await context.read<ApiClient>().post('/wallet/saved',
          data: {'kind': widget.kind, 'value': widget.value, 'institution': widget.institution});
      if (mounted) setState(() => _saved = true);
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(apiErrorMessage(e))));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return TextButton.icon(
      onPressed: _saved || _busy ? null : _save,
      icon: Icon(_saved ? Icons.star : Icons.star_border, color: SokoColors.orange),
      label: Text(_saved ? 'Saved' : 'Save recipient'),
    );
  }
}

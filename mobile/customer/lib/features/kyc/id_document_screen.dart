import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';
import 'package:sokopay_shared/sokopay_shared.dart';

/// Add an identity document from the Profile screen:
///   POST /kyc/documents  multipart {doc_type, number, expiry_date?, front, back?}
/// A Ghana Card is checked with NIA immediately (and raises the wallet to tier 1);
/// a passport or driver's licence is reviewed by SokoPay's compliance team.
/// Photos are sent once and not kept on the phone.
class IdDocumentScreen extends StatefulWidget {
  const IdDocumentScreen({super.key});
  @override
  State<IdDocumentScreen> createState() => _IdDocumentScreenState();
}

class _DocType {
  const _DocType(this.value, this.label, this.hint, this.needsExpiry, this.hasBack);
  final String value;
  final String label;
  final String hint;
  final bool needsExpiry;
  final bool hasBack;
}

const _types = [
  _DocType('ghana_card', 'Ghana Card', 'GHA-123456789-0', false, true),
  _DocType('passport', 'Passport', 'G1234567', true, false),
  _DocType('drivers_licence', "Driver's licence", 'As printed on the licence', true, true),
];

class _IdDocumentScreenState extends State<IdDocumentScreen> {
  _DocType _type = _types.first;
  final _number = TextEditingController(text: 'GHA-');
  DateTime? _expiry;
  Uint8List? _front;
  Uint8List? _back;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _number.dispose();
    super.dispose();
  }

  void _setType(_DocType t) => setState(() {
        _type = t;
        _number.text = t.value == 'ghana_card' ? 'GHA-' : '';
        _expiry = null;
        _back = null;
        _error = null;
      });

  Future<void> _pick(bool front) async {
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
    if (!mounted) return;
    setState(() {
      if (front) {
        _front = bytes;
      } else {
        _back = bytes;
      }
      _error = null;
    });
  }

  Future<void> _pickExpiry() async {
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate: _expiry ?? now.add(const Duration(days: 365)),
      firstDate: now.add(const Duration(days: 1)),
      lastDate: DateTime(now.year + 20),
      helpText: 'Expiry date on the document',
    );
    if (picked != null) setState(() => _expiry = picked);
  }

  String? _problem() {
    if (_number.text.trim().length < 5) return 'Enter the document number.';
    if (_type.needsExpiry && _expiry == null) return 'Add the expiry date.';
    if (_front == null) return 'Add a photo of the front.';
    return null;
  }

  Future<void> _submit() async {
    final problem = _problem();
    setState(() => _error = problem);
    if (problem != null) return;
    final api = context.read<ApiClient>();
    final nav = Navigator.of(context);
    final messenger = ScaffoldMessenger.of(context);
    setState(() => _busy = true);
    try {
      final e = _expiry;
      final form = FormData.fromMap({
        'doc_type': _type.value,
        'number': _number.text.trim(),
        if (e != null)
          'expiry_date': '${e.year}-${e.month.toString().padLeft(2, '0')}-${e.day.toString().padLeft(2, '0')}',
        'front': MultipartFile.fromBytes(_front!, filename: 'front.jpg'),
        if (_back != null) 'back': MultipartFile.fromBytes(_back!, filename: 'back.jpg'),
      });
      await api.post('/kyc/documents', data: form);
      messenger.showSnackBar(SnackBar(
          content: Text(_type.value == 'ghana_card'
              ? 'Ghana Card verified.'
              : "${_type.label} sent. We'll let you know when it's checked.")));
      nav.pop(true);
    } catch (e) {
      setState(() => _error = apiErrorMessage(e, fallback: "We couldn't send your document. Try again."));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _photo(String label, Uint8List? bytes, bool front) => Expanded(
        child: InkWell(
          onTap: _busy ? null : () => _pick(front),
          borderRadius: BorderRadius.circular(12),
          child: Container(
            height: 120,
            decoration: BoxDecoration(
              color: SokoColors.surface,
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: Colors.black12),
            ),
            clipBehavior: Clip.antiAlias,
            child: bytes == null
                ? Column(mainAxisAlignment: MainAxisAlignment.center, children: [
                    const Icon(Icons.add_a_photo_outlined, color: SokoColors.inkMuted),
                    const SizedBox(height: 6),
                    Text(label, style: const TextStyle(color: SokoColors.inkMuted)),
                  ])
                : Image.memory(bytes, fit: BoxFit.cover, width: double.infinity),
          ),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final e = _expiry;
    return Scaffold(
      appBar: AppBar(title: const Text('Add an ID document')),
      body: ListView(padding: const EdgeInsets.all(20), children: [
        SegmentedButton<String>(
          segments: [for (final t in _types) ButtonSegment(value: t.value, label: Text(t.label))],
          selected: {_type.value},
          showSelectedIcon: false,
          onSelectionChanged: _busy ? null : (v) => _setType(_types.firstWhere((t) => t.value == v.first)),
        ),
        const SizedBox(height: 16),
        TextField(
          controller: _number,
          textCapitalization: TextCapitalization.characters,
          autocorrect: false,
          decoration: InputDecoration(labelText: '${_type.label} number', hintText: _type.hint),
        ),
        if (_type.needsExpiry) ...[
          const SizedBox(height: 12),
          OutlinedButton.icon(
            onPressed: _busy ? null : _pickExpiry,
            icon: const Icon(Icons.event),
            label: Text(e == null
                ? 'Expiry date'
                : 'Expires ${e.day.toString().padLeft(2, '0')}/${e.month.toString().padLeft(2, '0')}/${e.year}'),
          ),
        ],
        const SizedBox(height: 16),
        Row(children: [
          _photo('Front', _front, true),
          if (_type.hasBack) ...[const SizedBox(width: 12), _photo('Back (optional)', _back, false)],
        ]),
        const SizedBox(height: 8),
        const Text('Lay the document flat in good light. All four corners visible, no glare, nothing covered.',
            style: TextStyle(color: SokoColors.inkMuted, fontSize: 12.5)),
        if (_type.value == 'ghana_card')
          const Padding(
            padding: EdgeInsets.only(top: 8),
            child: Text('We check your Ghana Card number with NIA straight away. Your name on SokoPay will then '
                'match your card.', style: TextStyle(color: SokoColors.inkMuted, fontSize: 12.5)),
          ),
        if (_error != null)
          Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(_error!, style: const TextStyle(color: SokoColors.danger))),
        const SizedBox(height: 20),
        ElevatedButton(
          onPressed: _busy ? null : _submit,
          child: _busy
              ? const SizedBox(
                  height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
              : Text(_type.value == 'ghana_card' ? 'Verify Ghana Card' : 'Send for checking'),
        ),
      ]),
    );
  }
}

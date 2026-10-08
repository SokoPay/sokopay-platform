import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:provider/provider.dart';
import 'package:sokopay_shared/sokopay_shared.dart';

/// Tier 1 → 2: take a live selfie with the front camera and send it for matching
/// against the Ghana Card photo. The image is uploaded once and not kept on the phone.
class SelfieScreen extends StatefulWidget {
  const SelfieScreen({super.key});
  @override
  State<SelfieScreen> createState() => _SelfieScreenState();
}

class _SelfieScreenState extends State<SelfieScreen> {
  XFile? _shot;
  bool _busy = false;
  String? _error;

  Future<void> _capture() async {
    final picker = ImagePicker();
    final shot = await picker.pickImage(
      source: ImageSource.camera,
      preferredCameraDevice: CameraDevice.front,
      maxWidth: 1280,
      imageQuality: 85,
    );
    if (shot != null) setState(() { _shot = shot; _error = null; });
  }

  Future<void> _submit() async {
    final shot = _shot;
    if (shot == null) return;
    final api = context.read<ApiClient>();
    final nav = Navigator.of(context);
    setState(() { _busy = true; _error = null; });
    try {
      final bytes = await shot.readAsBytes();
      final form = FormData.fromMap({
        'selfie': MultipartFile.fromBytes(bytes, filename: 'selfie.jpg'),
      });
      await api.post('/kyc/upgrade/selfie', data: form);
      if (mounted) nav.pop(true);
    } catch (e) {
      setState(() => _error = apiErrorMessage(e, fallback: 'Verification failed. Try again.'));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Selfie check')),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text('Take a clear selfie',
                style: TextStyle(fontSize: 20, fontWeight: FontWeight.w600)),
            const SizedBox(height: 8),
            const Text(
              'Good light, face the camera, no hat or sunglasses. We compare it with your '
              'Ghana Card photo and do not keep the picture.',
              style: TextStyle(color: Colors.black54),
            ),
            const SizedBox(height: 24),
            Expanded(
              child: Center(
                child: _shot == null
                    ? const Icon(Icons.face_retouching_natural, size: 120, color: Colors.black26)
                    : ClipRRect(
                        borderRadius: BorderRadius.circular(16),
                        child: Image.network(_shot!.path, fit: BoxFit.cover, // path works on web
                            errorBuilder: (_, __, ___) =>
                                const Icon(Icons.check_circle, size: 96, color: Colors.green)),
                      ),
              ),
            ),
            if (_error != null) ...[
              Text(_error!, style: const TextStyle(color: SokoColors.danger)),
              const SizedBox(height: 8),
            ],
            OutlinedButton.icon(
              onPressed: _busy ? null : _capture,
              icon: const Icon(Icons.camera_alt),
              label: Text(_shot == null ? 'Open camera' : 'Retake'),
            ),
            const SizedBox(height: 8),
            ElevatedButton(
              onPressed: (_busy || _shot == null) ? null : _submit,
              child: _busy
                  ? const SizedBox(height: 20, width: 20,
                      child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                  : const Text('Verify'),
            ),
          ],
        ),
      ),
    );
  }
}

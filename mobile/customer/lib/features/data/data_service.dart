import 'package:sokopay_shared/sokopay_shared.dart';

import '../bills/bills_service.dart';

/// Data bundles:
///   GET  /telcos/{network}/bundles
///   POST /payments/data   {telco, phone, bundle_code, source, network?, payer?}
/// The price is never sent by the app — the backend takes it from the catalogue.
class DataService {
  DataService(this._api);
  final ApiClient _api;

  Future<List<DataBundle>> bundles(String telco) async {
    final r = await _api.get('/telcos/$telco/bundles');
    return (r.data as List).map((e) => DataBundle.fromJson(e)).toList();
  }

  Future<PaymentResult> buy({
    required String telco,
    required String phone,
    required String bundleCode,
    required String source,
    String? network,
    String? payer,
    required String idempotencyKey,
  }) async {
    final r = await _api.post('/payments/data',
        data: {
          'telco': telco,
          'phone': phone,
          'bundle_code': bundleCode,
          'source': source,
          if (network != null) 'network': network,
          if (payer != null) 'payer': payer,
        },
        headers: {'Idempotency-Key': idempotencyKey});
    return PaymentResult.fromJson(r.data);
  }
}

class DataBundle {
  DataBundle({required this.code, required this.name, required this.price,
      required this.volume, required this.validity, required this.isSample});
  final String code;
  final String name;
  final String price;
  final String volume;
  final String validity;
  final bool isSample;
  factory DataBundle.fromJson(Map<String, dynamic> j) => DataBundle(
        code: j['code'],
        name: j['name'],
        price: j['price'],
        volume: j['volume'],
        validity: j['validity'],
        isSample: j['is_sample'] == true,
      );
}

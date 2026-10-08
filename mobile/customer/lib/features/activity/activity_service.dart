import 'package:sokopay_shared/sokopay_shared.dart';

/// The customer's activity feed — GET /activity (backend apps/activity). One newest-first
/// list over payments, transfers and wallet movements; amounts arrive pre-formatted.
class ActivityService {
  ActivityService(this._api);
  final ApiClient _api;

  /// [filter]: payments|transfers|wallet (null = all); [period]: today|7d|30d (null = all).
  Future<ActivityPage> page({String? filter, String? period, String? cursor}) async {
    final r = await _api.get('/activity', query: {
      if (filter != null) 'filter': filter,
      if (period != null) 'period': period,
      if (cursor != null) 'cursor': cursor,
    });
    return ActivityPage.fromJson(Map<String, dynamic>.from(r.data));
  }
}

class ActivityItem {
  ActivityItem({
    required this.id,
    required this.source,
    required this.category,
    required this.title,
    required this.subtitle,
    required this.direction,
    required this.amountDisplay,
    required this.status,
    required this.reference,
    required this.createdAt,
  });
  final String id;
  final String source; // payment | transfer | wallet
  final String category; // bill, airtime, data, shop, topup, transfer, sent, received, cash_in, …
  final String title;
  final String subtitle;
  final String direction; // in | out
  final String amountDisplay; // "+ GH₵ 10.00" / "− GH₵ 50.50"
  final String status; // succeeded | pending | failed | refunded
  final String reference; // SP-… for payments and transfers, else empty
  final DateTime createdAt;

  bool get isIn => direction == 'in';

  factory ActivityItem.fromJson(Map<String, dynamic> j) => ActivityItem(
        id: j['id'],
        source: j['source'] ?? '',
        category: j['category'] ?? '',
        title: j['title'] ?? '',
        subtitle: j['subtitle'] ?? '',
        direction: j['direction'] ?? 'out',
        amountDisplay: j['amount_display'] ?? '',
        status: j['status'] ?? '',
        reference: j['reference'] ?? '',
        createdAt: DateTime.parse(j['created_at']).toLocal(),
      );
}

class ActivityPage {
  ActivityPage({required this.results, this.nextCursor, this.moneyIn, this.moneyOut});
  final List<ActivityItem> results;
  final String? nextCursor;
  final String? moneyIn; // summary — first page only
  final String? moneyOut;

  factory ActivityPage.fromJson(Map<String, dynamic> j) {
    final s = j['summary'] == null ? null : Map<String, dynamic>.from(j['summary']);
    return ActivityPage(
      results: ((j['results'] ?? []) as List)
          .map((e) => ActivityItem.fromJson(Map<String, dynamic>.from(e)))
          .toList(),
      nextCursor: j['next_cursor'],
      moneyIn: s?['money_in_display'],
      moneyOut: s?['money_out_display'],
    );
  }
}

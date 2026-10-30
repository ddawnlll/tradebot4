# V8-core expert assessment for tradebot4

Tarih: 2026-10-30. Kaynak: `v8-core/src/experts/*.rs`, `docs/EXPERTS_REGISTRY.yaml`,
`~/src/tradebot_evaluation/v8_core_deep/INCELEME.md`. V8 sonuçları kullanılmış
pencere, küçük örnek ve `BURNED_DIAGNOSTIC` sınırlarıyla geçmiş geliştirme
verisidir; bağımsız OOS veya edge kanıtı değildir. Bu belge bir **seçim ve
uygulama planı**dır, performans iddiası değildir.

## 1. Beş aday

| Expert | Mekanizma | Veri / PIT | Yön ve invalidation | tradebot4 uyumu | V8 geçmişi |
|---|---|---|---|---|---|
| `failed_breakout_2b` | Significant swing low'un altına inip kapanışla üstüne dönme (failed breakdown) → LONG; simetrik SHORT. 2B non-failure swing. | `swing_low_10`, ATR, candle shape, 1m/15m closed bars | Kapanış seviyenin geri üstünde/altında; stop frozen swing seviyesinde; expiry 8 bar | Zone/pivot + yeni swing feature; range entry ile aynı bölgede ters yön | V8'de pilot; 17-49 işlemlik diğer ailelerle aynı burned pencerede değil, ayrı kanıt yok |
| `liquidity_sweep_reclaim` | Prior high/low iğneyle geçilip kapanışla geri alınır → reversal; stop swept level | Prior high/low, ATR, history | Close reclaimed side; stop swept level | `sweep_buffer` zaten var; en doğal ikinci aday | V8 pilot; aynı burned pencere sınırı |
| `breakout_retest` | Kırılan significant level retest edilir, rol değişimiyle devam | Location, ATR, history | Retest tutmazsa invalidation; expiry 8 | Range breakout sonrası; range entry ile çakışır | V8'de varyant a/b/c; örnek küçük |
| `ichimoku_cloud` | Tenkan(9)/Kijun(26) crossover + trend line | Volatility, history; cloud state içinde hesaplanır | Cross geri dönerse; expiry 8 | Trend rejimi gerekir; range-only motorda yeni rejim kapısı | İki ilişkili kayıtta 17-18 işlem, PF 3.2-3.8; çok küçük ve bağımsız değil |
| `trend_continuation` | EMA fast/slow trend + pullback + rejection | Trend EMA5/20, ATR, history | Slow EMA/stop; expiry 16 | Trend rejimi gerekir; range ailesiyle aynı anda çalışmaz | 42-49 işlem; iki koşuda +80 ve +13 USDT, değişken; edge değil |

## 2. Seçim

**Birinci aday: `failed_breakout_2b`.**

Nedenleri:
1. Audit'in en büyük kayıp kanalı BREAKOUT: 42 çıkış, -30.68 USDT, negatif
   PnL'in %90.8'i. Range kırılıp geri döndüğünde stop olmak yerine
   kırılışın başarısızlığını işlemek, aynı fiyat bölgesinde doğal bir karşıt
   mekanizma.
2. Gereksinimleri tradebot4'te mevcut veya bu turda eklendi: 1m OHLCV, ATR,
   onaylı pivot/zone, yeni `swing_low_10`/`swing_high_10` ve candle shape
   feature'ları. OI/L2/derivatives gerekmez.
3. Stop yapısal ve frozen: failed swing seviyesi; live-recomputed seviye
   kaydırılmaz. Bu, audit'teki "stop neden -1R'ı aşıyor" sorusunu ayrı test
   etmeyi sağlar.
4. Tek bir hipotez olarak 6 konfigle sınanabilir: swing penceresi 10/20,
   reclaim buffer 0/0.1 ATR, expiry 8/16 bar. Aynı anda 28 expert eklenmez.

**İkinci aday: `liquidity_sweep_reclaim`.** Aynı bölgede iğne+sweep mekanizması;
tradebot4'ün `sweep_buffer` modeliyle uyumlu. `failed_breakout_2b` sonucu
negatifse sıradaki tek aday; ikisi aynı anda çalıştırılmaz.

## 3. Neden bu turda uygulanmadı

- Bu turun P0 işi exit taraması ve tam Nautilus replay'iydi; exit sonucu
  bilinmeden yeni bir giriş ailesi eklemek, hangi değişikliğin ne yaptığını
  ayırmaz.
- Expert yeni bir strateji ailesidir: ayrı registry, ayrı hipotez, ayrı
  6 konfig, ayrı 4-fold ve random karşılaştırması gerekir. Mevcut range
  entry ile aynı sembolde ters yönde sinyal üretebilir; `max_positions=1`
  altında ikisini birlikte koşmak işlem setini ve sermaye yolunu değiştirir.
- V8'in `failed_breakout_2b` sonucu bu görevde bağımsız OOS olarak
  doğrulanmadı; "V8'de var" kabul gerekçesi değil.

**Durum: `NEXT_STEP` (veri engeli yok, zaman/öncelik engeli var).**

## 4. Uygulama planı (bir sonraki tur)

1. `FailedBreakout2BStrategy` (`StrategyBase`, `needs_range_setup=False`):
   - 15m entry barlarında son 20 barın onaylı swing low/high'ını bul.
   - Önceki bar seviyeyi ihlal etti, current close geri döndü → entry.
   - Stop = swing seviyesi ± sweep buffer; TP1 = 1R, TP2 = 2R veya tek 2R.
   - Expiry 8/16 bar; time stop.
2. Altı konfig: swing 10/20 × buffer 0/0.1 ATR × expiry 8/16 (6).
3. Ayrı 4-fold, 20 sembol, 20 USDT; aynı maliyet modeli, stress, random
   aynı exit; 100+ işlem, PF>=1.10, CI baseline üstü, plateau.
4. Range entry ile aynı hesapta çalıştırma; önce tek başına test et. Aynı
   anda iki aile gerekiyorsa portföy tahsisi ayrı bir karar.
5. Sonuç negatifse `liquidity_sweep_reclaim`'e geç; ikisini birden açma.

## 5. Uyarılar

- V8 `ichimoku_cloud`/`trend_continuation` pozitiflikleri 17-49 işlem,
  kullanılmış pencere ve ortak sermaye etkisi altında; bağımsız OOS değil.
- `failed_breakout_2b`'nin "failed" tanımı trend başlangıçlarını da yakalar;
  stop yapısal olsa bile ardışık kayıplar ve gap riski vardır.
- 20 USDT'de min notional/step, 15m entry ve 1m execution ile işlem sayısını
  sınırlar; 500 USDT ayrı senaryodur, ölçeklenmiş sonuç değildir.

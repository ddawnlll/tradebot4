# v8-core: tradebot4'e taşınabilecek parçalar

Tarih: 2026-10-30. Kapsam: `~/src/v8/v8-core` (Rust, ~88k LOC) ve
`src/v8`/`docs/contracts` referansları. Amaç: v8-core'daki hangi feature,
expert ve altyapı parçalarının tradebot4'ün mevcut motoruna gerçekten
taşınmaya değer olduğunu belirlemek.

**Kısa hüküm:** v8-core'da taşınmaya değer üç katman var: (1) exit/risk
politikaları, (2) 77 feature'lık state kataloğunun küçük bir alt kümesi,
(3) 28 behavior expert'ten 2-3 tanesi. İlk iki hafta **exit ablation + chop
gate + capital envelope** ile başlanmalı; 77 feature'ın tamamı aynı anda
eklenmemeli. v8-core quarantine'de, economic claim'i yok ve feature'lar
kitap-türevi; bunlar kanıt değil, aday hipotezler.

## 0. Neden önce exit?

tradebot4 audit'i net: dev-A'da 42 BREAKOUT çıkışı -30.68 USDT, tüm negatif
PnL'in %90.8'i; TP2 + breakeven + TP1 kazançları +25.92 USDT. Giriş
maliyetini düşürmek (H1a) PF'i 0.919'a çıkardı ama hâlâ negatif. Yani en
büyük kaldıraç giriş feature'ı değil, **kaybın gerçekleştiği çıkış kuralı**.
v8-core'un `exit_ablation.rs` + `kaizen/exit_trailing.rs` tam bu soruya
bakıyor.

## 1. v8-core envanteri (ilgili alt küme)

| Modül | LOC | Ne yapıyor | tradebot4 için değer | Öncelik |
|---|---:|---|---|---|
| `src/state.rs` | 3,747 | 77 feature, çoklu timeframe, causal frame, feature store | Feature kataloğu | P1 |
| `src/experts/*.rs` | 8,055 | 28 behavior family, setup anchor + risk geometry | 2-3 expert | P2 |
| `src/simulator.rs` + `backend/scalar.rs` | 1,733 | R-multiple replay, stop-first, gap fill, funding-before-orders | Exit/risk geometrisi | P1/P2 |
| `src/exit_ablation.rs` | 601 | Faktöriyel exit policy ablation + rejim dilimleme + TCA | **P0** | P0 |
| `src/kaizen/exit_trailing.rs` | 492 | 12 trailing/static exit kolu | **P0** | P0 |
| `src/kaizen/chop_suppression.rs` | 236 | Cost-aware no-trade gate, cooldown, expansion kalitesi | **P0/P1** | P0 |
| `src/usdm_sim/capital_viability.rs` | 242 | E_crit = max(step, notional, margin) sermaye zarfı | Audit tanısı | P0 |
| `src/usdm_sim/scenario_ruin.rs` | 254 | Bootstrap ruin, time-to-ruin, SaR | Risk tanısı | P1 |
| `src/analysis/*.rs` | 1,460+ | Outcome cube, regret faz 1-3, veto attribution | P2 | P2 |
| `src/oracle/recoverability.rs` | 191 | Hindsight → PIT → promotable → live waterfall | P3 (sabitler heuristic) | P3 |
| `src/world/*.rs` | - | Cross-asset, structural break, stationary bootstrap | Stress testleri | P3 |
| `src/assurance/*`, `judiciary/*`, `benchmark/*` | - | Governance/evidence/benchmark fabric | Hayır | - |

Toplam ilgili alt küme ~7.7k LOC + 8k expert kodu. Rust'ı port etmek değil,
**formülleri ve deney tasarımını** Python'a taşımak gerekir.

## 2. Feature gap matrisi

v8-core `state.rs:54-136` 77 feature adı; `state.rs:1040-1315` formüller.

| Grup | v8-core feature'ları | tradebot4'te şimdi | Gap | Öncelik |
|---|---|---|---|---|
| raw/trend | close, ema_fast(5), ema_slow(20) | close, ATR, ADX, Choppiness, BBW percentile | EMA yok | P1 |
| volatility | atr, bb_mid/upper/lower, bb_pct_b, bb_bandwidth, atr_locational, atr_filtered_2sigma, atr_2sigma_active, atr_trend_phase, keltner_u/l, starc_u/l, atr_band_stop | ATR, BBW percentile (regime içinde) | pct_b, Keltner/STARC, 2sigma, trend phase, band stop | P0/P1 |
| location | prior_high/low, swing_high/low 5/10/20, window_high/low 10/20/50, range_height, fib_levels, pivot_points_day, consolidation_range, gap_levels, atr_band_stop | Pivot/zone (onay gecikmeli) | prior prefix, Donchian, çoklu swing, Fib, pivot points, consolidation, gap zones | P0/P1 |
| candle_shape | real_body, body_range_ratio, upper/lower_shadow, close_position, inside_bar, outside_bar, gap_size, gap_dir | OHLC var; stratejide sadece wick oranı | feature olarak yok | **P0** |
| oscillator | rsi14, stoch_k/d, stochrsi, cci20, macd/signal/hist, mom_14, roc_14, adx14, osc_obos_quantile | ADX14 | RSI/Stoch/CCI/MACD/ROC/quantile yok | P1 |
| participation | volume, vol_zscore, vol_min_proximity, vol_smooth_ma, obv, adl, cmf_20, vwap, bar_class | volume, median/24h quote volume | z-score, CMF, OBV/ADL, VWAP, bar class | P0/P1 |
| session | hour_of_day_utc, impulsive_window, bar_of_session, day_index | sadece by_hour metrik | feature değil | P1 |
| positioning | funding_rate, open_interest, long_short_skew | funding PnL'de var; feature değil | funding z-score; OI/skew veri yok | P1/P2 |
| history | 32-bar tuple | deque'ler var | gerek yok | - |
| response | (expert içinde) | wick/rejection var | manual composite | P1 |

## 3. İlk port edilecek 11 feature

Bunlar tek başına edge değil; her biri bir hipoteze bağlanmalı.

| # | Feature | Formül (v8-core) | tradebot4 kullanımı | Efor |
|---|---|---|---|---|
| 1 | `body_range_ratio`, `close_position`, `upper/lower_shadow` | body/range, (close-low)/range, wick uzunlukları | Rejection kalitesi, entry/exit onayı | Düşük |
| 2 | `inside_bar`, `outside_bar` | önceki barın high/low'u ile karşılaştırma | Sıkışma/genişleme, breakout öncesi | Düşük |
| 3 | `bb_pct_b`, `bb_bandwidth` | SMA20 ± 2σ; (close-lower)/(upper-lower); (upper-lower)/mid | Range kenarı ve sıkışma | Düşük |
| 4 | `atr_2sigma_active`, `atr_trend_phase` | ATR14 > SMA20(ATR)+2σ; sign(ATR_t - ATR_{t-10}) | Volatilite patlaması / breakout tail filtresi | Düşük |
| 5 | `consolidation_range` | 20 bar öncesinin high/low'u; width<=%3 ve close içinde | Coiling range veto | Düşük |
| 6 | `prior_high`, `prior_low`, `window_high/low_20/50` | prefix max/min; son N barın max/min'i | Yüksek timeframe location | Düşük |
| 7 | `swing_high/low_10/20` | n=10/20, iki yanlı pivot + range>=1.0 ATR | Yapısal seviyeler, stop referansı | Orta |
| 8 | `vol_zscore`, `cmf_20` | (vol-mean100)/std100; Chaikin Money Flow 20 | Gerçek iştirak vs fake touch | Düşük |
| 9 | `vwap` | gün başından beri TP*vol / vol | Mean-reversion anchor | Düşük |
| 10 | `rsi14`, `stoch_k/d`, `macd_hist` | Wilder RSI14; Stoch14; MACD 12/26/9 | Confluence/divergence | Orta |
| 11 | `hour_of_day_utc`, `impulsive_window` | UTC saat; 08-17 UTC | Session filtresi, by_hour açıklaması | Trivial |

**Yapılmaması gereken:** 77 feature'ın tamamını aynı anda eklemek. Feature
sayısı arttıkça multiple-testing ve overfit riski artar; tradebot4'ün
4-fold/cost/random gate'i bunu cezalandırır. Her batch en fazla 6 konfig.

## 4. İlk port edilecek 3 expert

v8-core `experts/` 28 aile; tradebot4'ün range ailesine en yakın ve audit'in
breakout tail'ine doğrudan cevap veren üçü:

### 4.1 `failed_breakout_2b` (öncelik 1)
- Mekanizma: significant swing low'un altına inip tekrar üstüne kapanma
  (failed breakdown) → LONG; simetrik SHORT. v8-core varyant b, 2B
  (Sperandeo). `src/experts/failed_breakout_2b.rs`.
- Gerekli feature'lar: `swing_low_10`, ATR, history, candle_shape.
- Neden tradebot4: 42 breakout çıkışı -30.68 USDT. Range kırılıp geri
  döndüğünde stop olmak yerine **kırılışın başarısızlığını** işlemek,
  mevcut ailenin doğal tamamlayıcısı.
- Riski: failed breakout'ların çoğu gerçek trend başlangıcı olabilir;
  stop yapısal seviyede, 1R hedef; ayrı bir strateji olarak test edilmeli,
  range entry'ye filtre olarak yapıştırılmamalı.

### 4.2 `liquidity_sweep_reclaim` (öncelik 2)
- Mekanizma: önceki high/low'un iğneyle geçilip kapanışla geri alınması;
  stop süpürülen seviyede. `src/experts/liquidity_sweep_reclaim.rs`.
- Gerekli: prior high/low, ATR, history.
- Neden: tradebot4 zaten `sweep_buffer` ile stop süpürmesini modelliyor;
  bunu entry'ye çevirmek mantıklı. H1a'da %65 fill, ters seçim -0.33%;
  sweep reclaim, limit dolmayan yerlerde fırsat olabilir.
- Riski: iğne + reclaim çok sık; cooldown ve rejim filtresi gerekir.

### 4.3 `breakout_retest` (öncelik 3)
- Mekanizma: kırılan seviyenin retestinde rol değişimi; ilk breakout'u
  kovalamak yerine geri çekilmeyi bekler. `src/experts/breakout_retest.rs`.
- Gerekli: location, ATR, history.
- Neden: breakout tail'ini azaltmanın bir yolu, breakout'a hiç girmemek;
  retest, daha iyi fiyat ve daha net stop verir.
- Riski: retest hiç gelmezse işlem kaçar; fill rate düşer.

Alternatif: trend rejiminde `trend_pullback_depth`; ancak tradebot4 şu an
range-only, rejim kapısını açmak ayrı bir karar.

## 5. Altyapı modülleri

### 5.1 Exit policy ablation (P0)
`src/exit_ablation.rs`, `src/kaizen/exit_trailing.rs`.
- 12 kol: Static1R/2R/3R, NoTP, ChandelierATR, EMA4hTrail, HybridTrail,
  Chandelier+BE 0.5/0.75/1.0R, Structural24h/48h Trail.
- Tradebot4'te ilk adım: mevcut 103 baseline trade'in entry/stop/risk
  geometrisini sabit tutup, 1m fiyat yolu üzerinde her kolu yeniden
  simüle etmek. Bu, girişleri yeniden çalıştırmadan **sadece exit etkisini**
  ölçer. Çıktı: net R, PF, tail capture, max DD, exit reason dağılımı.
- Kabul: mevcut TP1/TP2/breakeven/breakout çıkışından daha iyi PF ve
  daha düşük breakout kaybı; 4-fold ve cost stress ile doğrulanmalı.

### 5.2 Chop suppression / no-trade gate (P0)
`src/kaizen/chop_suppression.rs`.
- A1: expected gross excursion > 2.5 × friction (R cinsinden).
- A2: aynı yönde son başarısız kampanyadan sonra 8 bar cooldown.
- A3: rolling volatility compression ratio >= 0.65 (ölü sıkışma veto).
- A4/A5: kombinasyonlar.
- Tradebot4'ün width/cost ratio'su var ama expected excursion ve
  failure-based cooldown yok. Bu, mevcut 103 trade'e offline uygulanıp
  hangi işlemleri eleyeceği ölçülebilir; yeni bir H4 hipotezi.

### 5.3 Capital viability envelope (P0, tanı)
`src/usdm_sim/capital_viability.rs`.
- `E_step = step_size * stop_distance / risk_fraction`
- `E_notional = min_notional * stop_distance / (entry_price * risk_fraction)`
- `E_margin = min_notional / leverage`
- `E_crit = max(E_step, E_notional, E_margin)`.
- 20 USDT hesapta BTC gibi pahalı sembollerde step-size kısıtı E_crit'i
  20 USDT üstüne çıkarabilir. Baseline'da sadece 1 MIN_QUANTITY reddi var,
  yani ana neden değil; ama audit'e "hangi sembollerde yapısal olarak
  tradable değiliz" satırı ekler. Port eforu 1 gün.

### 5.4 Outcome/regret (P2)
`src/analysis/*`, `src/oracle/recoverability.rs`.
- Faydalı kavram: her candidate için counterfactual outcome, reddedilen
  adayların sonucu, exit alternatiflerinin sonucu, PIT/multiplicity/friction
  ayrıştırması.
- Ancak `oracle/recoverability.rs` 45%/55% gibi sabit heuristic'ler
  içeriyor; bu sabitler kanıt değil. tradebot4'te önce basit "what if"
  analizi yapılmalı: her trade için stop/target alternatifi, her REJECT
  sinyali için ileri getiri. `missed_opportunities` zaten var; genişlet.

### 5.5 MarketState PIT/quality (P2)
`MARKET_STATE_CONTRACT.md`, `state.rs` FeatureStore.
- 4 clock, availability, quality, null_reason, state hash, feature graph
  version. tradebot4'in 1m bar verisi daha basit; bar close + sabit gecikme
  yeterli. Veri boşlukları/geç güncellemeler sorun olursa eklenmeli.
- Feature version/lineage, araştırma tekrarlanabilirliği için faydalı;
  kâr için doğrudan kaldıraç değil.

## 6. Port edilmeyecekler

- 28 expert'in tamamı; feature dump; aynı anda 77 feature.
- `assurance/`, `judiciary/`, `benchmark/`, `tevv/` governance katmanı.
- v8-core'un tam Rust engine'ini Nautilus'un yerine koymak.
- `usdm_sim/maker_model.rs` (markout'lar 2.5/4.2/1.8 bps hardcoded,
  MODEL_DERIVED) ve `oracle/recoverability.rs` sabitleri kanıt gibi
  kullanılmamalı.
- OI/long-short skew (v8'de DATA_BLOCKED; tradebot4'te veri yok).
- `history` feature (tradebot4'te deque'ler var).

## 7. İki haftalık plan

**Hafta 1**
1. Gün 1-2: `exit_ablation` offline. 103 baseline trade, 12 kol, 1m path.
   Çıktı: `reports/research/exit_ablation.md` + JSON. Preregister.
2. Gün 3: feature batch 1'i `SymbolState`'e ekle: candle shape, bb_pct_b,
   bb_bandwidth, atr_2sigma_active, atr_trend_phase, consolidation_range,
   prior_high/low, window_high/low_20/50, vol_zscore, cmf_20, vwap.
   Sadece feature olarak; karar yoluna bağlama.
3. Gün 4: chop gate A1/A2/A3/A5'i offline uygula; kaç işlem elenir,
   PF/exp nasıl değişir.
4. Gün 5: capital envelope + audit entegrasyonu; rapor.

**Hafta 2**
5. Exit ablation'ın en iyi 2 kolunu tam backtest'e al; 4-fold, cost stress,
   random benchmark, 100+ trade, PF>=1.10, plateau. Geçmezse dur.
6. Feature batch 1'i range entry'ye **tek tek** filtre olarak dene
   (max 6 konfig); her feature için ayrı hipotez, aynı gate.
7. Sonuçları `kaizen/experiments.parquet` ve audit history'ye kaydet.

**Kabul kriteri:** 4-fold, 100+ işlem, PF>=1.10, stress PF>=1.00,
CI baseline'ın üstünde, random'ı yen, plateau. Geçmeyen batch raporlanır
ve durdurulur; parametre gezintisi yapılmaz.

## 8. Riskler ve uyarılar

- **v8-core quarantine'de ve NO_ECONOMIC_CLAIM.** Feature'lar kitap-türevi;
  hiçbiri tradebot4'te doğrulanmış değil. "v8'de var" edge kanıtı değil.
- **Timeframe uyumu:** v8-core 1h tape üzerinde; tradebot4 15m/1h/4h.
  `pivot_points_day` 1h'de 24 bar, 15m'de 96 bar. Swing/window feature'ları
  bar sayısına bağlı; her timeframe için ayrı sabit gerekir.
- **Veri:** funding var; OI/long-short yok. Maker markout için L2 yok.
- **Multiple testing:** 77 feature × 6 konfig = ciddi overfit riski.
  Preregistration, tek batch, max 6 konfig, 4-fold, cost stress.
- **Maliyet:** feature eklemek backtest süresini artırır; tradebot4'ün
  58k bar/s ve 1.7 GiB tepe RSS'i göz önünde tutulmalı.

## 8.5 Hangi timeframe'de hesaplanmalı

v8-core tek bir 1h tape üzerinde çalışıyor; tradebot4 15m entry, 1h context,
4h higher kullanıyor. Feature'ları doğru katmana koymak gerekir:

| Katman | Feature'lar | Neden |
|---|---|---|
| 15m entry | candle shape, bb_pct_b, rsi14, stoch_k/d, vol_zscore, cmf_20, vwap | Giriş/çıkış zamanlaması, rejection kalitesi |
| 1h context | atr_2sigma_active, atr_trend_phase, bb_bandwidth, consolidation_range, swing_high/low_10/20 | Rejim, sıkışma, yapısal seviyeler |
| 4h higher | prior_high/low, window_high/low_20/50, pivot_points_day, fib_levels | Yüksek timeframe location, range'in nerede olduğu |

`pivot_points_day` 1h'de 24 bar, 15m'de 96 bar; `swing_*` ve `window_*`
bar sayısına bağlı olduğu için her timeframe'de ayrı sabit gerekir.

## 8.6 28 expert kataloğu (v8-core `experts/`)

| Expert | Mekanizma (kısa) | Requires | tradebot4 uyumu |
|---|---|---|---|
| trend_pullback | Trend içinde geri çekilme devamı | trend, volatility, history | Orta; trend rejimi kapalı |
| failed_breakout | Prior high üstüne kapanış, sonra altına dönüş → SHORT | location, volatility, history | **Yüksek**; breakout tail |
| liquidity_sweep_reclaim | Prior extreme iğne + kapanışla reclaim → reversal | location, volatility, history | **Yüksek**; sweep buffer var |
| breakout_retest | Kırılan seviyenin retestinde devam | location, volatility, history | Orta-yüksek |
| capitulation | Data blocked (derivatives tape) | trend, volatility, history | Veri yok |
| failed_breakout_2b | 2B failed breakdown/swing reclaim | location, volatility, history, candle_shape | **Yüksek** |
| trend_pullback_depth | Derin pullback + reclaim | trend, location, volatility, history | Orta |
| range_breakout_1to1 | Consolidation breakout, 1:1 hedef | location, volatility, history, participation | Orta |
| candlestick_reversal | Hammer/engulfing vb. bar formasyonu | candle_shape, volatility, history | Düşük-orta |
| rsi_stoch_reversion | Aşırı alım/satım dönüşü | oscillator, volatility, history | Orta |
| macd_stoch_trend | Stoch cross + MACD filtresi | oscillator, volatility, history | Orta |
| divergence_12_setups | Momentum divergence | oscillator, location, volatility, history | Orta |
| bollinger_breakout | SMA dışına kapanış, 2SD hedef | volatility, history | Orta |
| bollinger_reversion | 2SD'den 1SD'ye dönüş | trend, volatility, history | Orta |
| donchian_breakout | N-bar kanal kırılımı | location, volatility, history | Orta |
| fib_retracement_continuation | Fib seviyesine pullback + reclaim | location, volatility, history | Orta |
| fib_projection_reversal | Fib extension'da reversal | location, volatility, history | Düşük-orta |
| pattern_measuring_objective | Geometrik formasyon hedefi | location, volatility, history | Düşük |
| volume_confirmed_breakout | Hacim onaylı breakout | location, volatility, participation, history | Orta |
| volume_climax_reversal | Hacim klimaksi + dönüş | trend, volatility, participation, history | Orta |
| obv_adl_regime | OBV/ADL rejimi | participation, trend | Düşük-orta |
| ichimoku_cloud | Tenkan/Kijun + bulut | volatility, history | Düşük-orta |
| floor_trader_pivot | Günlük pivot reaksiyonu | location, volatility, history, session | Orta |
| market_profile_value_area | POC/value area reversion | session, volatility, history | Orta-yüksek (profil gerekir) |
| gap_exhaustion | Gap tükeniş/breakaway/runaway | candle_shape, location, volatility, history | Orta |
| open_interest_divergence | OI/fiyat diverjansı | positioning, participation, volatility, history | Veri yok (DATA_BLOCKED) |
| funding_crowding_reversal | Funding aşırı kalabalık → reversal | positioning, volatility, history | Orta; funding var |
| pandf_breakout | Point & Figure box kırılımı | volatility, history | Düşük |
| fib_rsi_bb_confluence | Fib + RSI + BB üçlü konfluens | oscillator, location, volatility, history | Orta |
| squeeze_swing | Sıkışma sonrası swing | volatility, history | Orta |

**Öncelik:** `failed_breakout_2b`, `liquidity_sweep_reclaim`,
`breakout_retest`. Diğerleri ancak bu üçü ve exit ablation bittikten sonra.

## 9. Kaynak referansları

- `v8-core/src/state.rs:54-136` (77 feature adı), `:1040-1315` (formüller).
- `v8-core/src/experts/failed_breakout.rs`,
  `failed_breakout_2b.rs`, `liquidity_sweep_reclaim.rs`,
  `breakout_retest.rs`.
- `v8-core/src/exit_ablation.rs`, `src/kaizen/exit_trailing.rs`,
  `src/kaizen/chop_suppression.rs`.
- `v8-core/src/usdm_sim/capital_viability.rs`,
  `usdm_sim/scenario_ruin.rs`.
- `v8-core/src/simulator.rs` (R-multiple geometry), `src/analysis/mod.rs`,
  `src/oracle/recoverability.rs`.
- `docs/contracts/MARKET_STATE_CONTRACT.md`,
  `docs/EXPERTS_REGISTRY.yaml`.

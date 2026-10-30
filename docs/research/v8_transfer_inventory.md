# V8-core → tradebot4 transfer inventory

Tarih: 2026-10-30. Bu belge bir **envanter ve sınıflandırma** kaydıdır; bir
özelliğin V8'de bulunması onun edge veya OOS kanıtı olduğu anlamına gelmez.
Kaynak repo salt-okunur incelendi, değiştirilmedi.

## 0. Kimlik

### tradebot4 (hedef)

| Alan | Değer |
|---|---|
| Başlangıç HEAD | `6e7483c` (`main`, başlangıçta temiz) |
| Görev sırasında commit'ler | bu belge ve transfer işleri commit edilirken SHA raporlanır |
| Baseline validation | `reports/validation_20261030T115653Z/validation.json` |
| Baseline run | `20261030T115653Z_range_v1_dacaa5eb69700faa` |
| Config hash | `dacaa5eb69700faa` |
| Engine | NautilusTrader `2.0.0rc8`, Python 3.13.9 |
| Seed | `42` |
| Symbols | 40 USDT-M perpetual (universe `universe_2024-11-01_top40.parquet`) |
| Development | 2024-11-01T00:00:00Z .. 2026-07-01T00:00:00Z (`dev-A` 2024-11..2025-12, `dev-B` 2026-01..06) |
| Holdout (sealed) | 2026-07-01 .. 2026-10-20, manifest `4768406fa559fc4fc0f2124fc946a262` |
| Account | 20 USDT, leverage 10, risk 5%/trade, two-leg TP, kill switch 50% |
| Costs | maker 2 bps, taker 5 bps, market slip `max(2 bps,1 tick)`, stop 5 bps + gap, limit 1 tick through + 90% touch |
| 1m data hash | `7df0d426faa21c9e86a91a7c6a1a38b00580a669faf6ad80553e51d45720c18d` (959 parquet files, sorted path+content digest) |
| Funding hash | `92f86a0601b195d4d1746039d55b3828c9ca311fbd03760cb1da349215ce62c8` |
| Universe hash | `428b9988de4fe289da5e5589763e801a9599438135f71260a346633331977a95` |
| Instruments hash | `9980f9102d17a8670edb2f004e4f83c6ecbeb3e51ff1d7f480f6ff52089d12b8` |
| Gates hash | `6286b90462d8f4a33fe915dcbff88e2d45e23275fa9c2025b3ab64367b32c782` |
| Data split hash | `5a6fd599bb8b87607ca67d26129e3a0e19746219052d904676745292e26da10e` |

Baseline (dev-A, 103 trade): net **-7.8536**, PF **0.7674**, expectancy
**-0.0838R**, max DD **50.45%**, fees **4.252**, slippage **3.302**, funding
**-0.0764**, gross-before-slippage **-0.223**, net R sum **-8.6294**.
4-fold baseline: 253 trade, net -34.77, PF 0.625, -0.1697R, 0/4 positive;
random: 198 trade, -29.70, PF 0.784, -0.1482R. Bu iddialar mevcut
`reports/validation_20261030T115653Z`, `reports/research_round1/summary.json`
ve run artefaktlarıyla eşleşir; bu görevde yeniden üretilmedikçe **önceki koşu
kanıtı** olarak etiketlenir.

### V8 kaynak (salt-okunur)

| Alan | Değer |
|---|---|
| V8 HEAD (görev başında) | `e49e09f1797bca7001253ad81ffdd918c394fe41` |
| V8 HEAD (görev sırasında) | `ee60eac79628ed45c0a4ff597501cf98db68aea1`; final gözlem `f9b701ff282e49ac3487b3d16b247662b2378905` (repo paralel gelişiyor, ilgili dosya hashleri değişmedi) |
| v8-core son commit | `0990615962431ca8434824b3be33ac076a69f3bf` (2026-09-11, wip) |
| Dirty tracked dosya | 48 (ağırlıkla agent/rules/docs; aşağıdaki modüller clean) |
| `state.rs` | `0146e900421fb53f78642b500b1866162a1513c259b72c8eeb79df2beba4e1c7` |
| `exit_ablation.rs` | `49ee27ce7cf22a872614ed1c57f37b0d5eaf7adc5d81c43ef3d5c13a7872667e` |
| `kaizen/exit_trailing.rs` | `3443224e7c014f6d8d9721bad0bc7f6677a2d96de8a57a2334a15d5d0eb6cefa` |
| `kaizen/chop_suppression.rs` | `9468a644479cff9d094e9858009253e046fc5d73ca878d6c50b99f2df1c25bdc` |
| `usdm_sim/capital_viability.rs` | `4c514b038c5b62a6768fda28753ce0fb2648ccf7dcd285af44dc9be4ecfeff96` |
| `usdm_sim/scenario_ruin.rs` | `91ad14fbb3e4b54165aee9b1e5e57f56792cf4177a7d8745b2c4500705f9866e` |
| `experts/failed_breakout_2b.rs` | `7f8b52394a78a33054ac5fe58d3de8c54861bed6c43e31ddcf61a2751967a05b` |
| `experts/liquidity_sweep_reclaim.rs` | `058dacade872b5b810154379a3c7d8ac9865bd7f552dfe466e3ecbec9347adae` |
| `experts/breakout_retest.rs` | `d39aab6302eb7418f486e700540edcea9734eae9d23b0983db1af0a3d7f6f659` |

**Uyarı:** v8-core'un 106 BTC işlemi/+56 USDT, 1.000 USDT, `BURNED_DIAGNOSTIC`,
sıfır raporlanmış slippage ve kullanılmış pencere sınırlarıyla geçmiş geliştirme
verisidir. 2 bps/leg ek maliyetle +19 USDT'ye, ~3.03 bps/leg ile sıfıra
düşmektedir. Edge veya bağımsız OOS kanıtı değildir. Seçici inceleme:
`~/src/tradebot_evaluation/v8_core_deep/INCELEME.md`.

## 1. Sınıflandırma lejantı

| Durum | Anlamı |
|---|---|
| `MEVCUT` | tradebot4'te çalışan, aynı fikri zaten uygulayan kod |
| `UYARLA` | formül/fikir uyarlanabilir, veri ve PIT koşulu net |
| `DENEY` | yeni hipotez/ablation olarak test edilmeli, varsayılan karar yoluna girmez |
| `VERİ_ENGELLİ` | gerekli veri yok veya tarihsel geçerliliği UNKNOWN, uydurma yapılmayacak |
| `YİNELENEN` | tradebot4'te eşdeğeri var, tekrar port gereksiz |
| `ERTELENEN` | değerli ama bu turda kapsam dışı, gerekçeli |
| `PORT_EDİLMEZ` | governance/engine/veri açısından taşınmamalı |

## 2. MarketState / feature envanteri

Kaynak: `v8-core/src/state.rs:54-136` (77 isim), `:1040-1315` (formüller),
`docs/contracts/MARKET_STATE_CONTRACT.md`. tradebot4 hedefi:
`src/bot/core/market/state.py:55` (`SymbolState`), `:106` (`_route`), yeni
`src/bot/core/features.py` (gözlemsel batch).

| V8 grubu / feature | Durum | tradebot4 mevcut | Hedef / koşul | Maliyet / risk |
|---|---|---|---|---|
| raw close | `MEVCUT` | `Bar.close`, state | - | - |
| trend `ema_fast(5)`, `ema_slow(20)` | `UYARLA` | ADX/Chop/BBW var, EMA yok | `features.py`, 15m/1h | Düşük; trend rejimi kapalıysa tek başına karar değiştirmemeli |
| volatility ATR14, BB mid/upper/lower, `bb_pct_b`, `bb_bandwidth` | `MEVCUT/UYARLA` | ATR14, BBW percentile var; pct_b/band yok | 15m features, range edge/compression | Düşük; BBW zaten regime'de kullanılıyor, çift sayım riski |
| volatility `atr_2sigma_active`, `atr_trend_phase`, `atr_filtered_2sigma`, `atr_locational` | `DENEY` | yok | 1h features; breakout tail filtresi | Düşük; 2σ eşiği kanıt değil, aday |
| volatility Keltner/STARC/ATR band stop | `ERTELENEN` | yok | 15m/1h | Düşük; range ailesine doğrudan katkısı belirsiz |
| location `prior_high/low` | `DENEY` | zone/pivot var, prefix yok | 4h/1h features | Düşük; PIT prefix testi şart |
| location `window_high/low_10/20/50`, `range_height_*` | `DENEY` | Donchian yok | 4h features | Düşük; bar sayısı timeframe'e bağlı |
| location `swing_high/low_5/10/20` significance | `DENEY` | pivot/zones var, çoklu significance yok | 1h/4h features | Orta; sağ taraf onayı ve ATR filtresi test edilmeli |
| location `consolidation_range` | `DENEY` | BBW percentile var, 20-bar width yok | 1h features | Düşük; coiling range vetosu |
| location `fib_levels`, `pivot_points_day`, `gap_levels` | `ERTELENEN` | yok | 4h/1h | Orta; pivot günü 1h'de 24, 15m'de 96 bar |
| candle `real_body`, `body_range_ratio`, shadows, `close_position` | `UYARLA` | `Bar` alanları var, feature değil | 15m features | Düşük; rejection/exit onayı |
| candle `inside_bar`, `outside_bar`, `gap_size/dir` | `DENEY` | gap sayacı var, feature yok | 15m features | Düşük; sıkışma/genişleme |
| oscillator RSI14, Stoch K/D, StochRSI, CCI20, MACD, MOM/ROC, ADX14, OBOS quantile | `DENEY` | ADX14 var; diğerleri yok | 15m features | Orta; çoklu indicator overfit riski |
| participation `volume`, `vol_zscore`, `vol_min_proximity`, `vol_smooth_ma`, `obv`, `adl`, `cmf_20`, `vwap`, `bar_class` | `DENEY` | volume/median/24h var; z/CMF/OBV/ADL/VWAP yok | 15m features | Düşük/orta; flow filtresi |
| session `hour_of_day_utc`, `impulsive_window`, `bar_of_session`, `day_index` | `UYARLA` | sadece `by_hour` metrik | 15m features | Trivial; filter değil, gözlem |
| positioning `funding_rate` | `UYARLA` | funding PnL'de var, feature değil | 1h/8h feature | Düşük; funding publication/settlement PIT |
| positioning `open_interest`, `long_short_skew` | `VERİ_ENGELLİ` | yok | veri yok | Uydurma yok |
| `history` 32-bar tuple | `YİNELENEN` | deque'ler var | - | - |
| response (expert composite) | `DENEY` | wick/rejection var | strateji içi | Orta; tanımı expert'e bağlı |

**Karar:** 77 feature'ın tamamı port edilmez. İlk gözlemsel batch:
candle shape, `bb_pct_b`/`bb_bandwidth`, `atr_2sigma_active`/`atr_trend_phase`,
`consolidation_range`, `prior_high/low`, `window_high/low_20/50`,
`vol_zscore`, `cmf_20`, `vwap`, `hour_of_day_utc`/`impulsive_window`.
Her feature `value`, `available_time_ms`, `feature_version`, `quality`,
`null_reason`, `input_ts` taşır; varsayılan strateji değişmez.

## 3. Exit politikaları

Kaynak: `v8-core/src/kaizen/exit_trailing.rs:5-30` (12 kol),
`:180-350` (Chandelier/BE/structural), `src/exit_ablation.rs:20-60`
(faktöriyel ablation), `src/simulator.rs:1-40` (stop-first, gap, funding
settlement, MAE/MFE). tradebot4: `engine.py:370` exit, `range_v1.py:200`
breakout/time, `bridge.py:210` protection.

| V8 kolu | Durum | tradebot4 karşılığı | Not |
|---|---|---|---|
| baseline TP1/TP2/BE/breakout | `MEVCUT` | `RangeV1` + bridge | referans |
| no breakout, protective stop | `DENEY` | `RangeV1Config.enable_breakout_exit=False` | offline + full replay |
| static 1R/2R/3R | `DENEY` | `BacktestConfig.static_target_r` eklendi | offline + full replay |
| no TP / stop+time | `DENEY` | static target yok; time stop var | offline diagnostic |
| Chandelier ATR 2.5/3.0 | `DENEY` | yok | offline diagnostic; full replay için trailing stop gerekir |
| BE 0.5/0.75/1.0R | `DENEY` | TP1 sonrası BE var, MFE BE yok | offline diagnostic |
| Hybrid trail | `ERTELENEN` | yok | BE+Chandelier birleşimi; önce basit kollar |
| EMA4h trail | `ERTELENEN` | yok | EMA/pass-through tanımı netleşmeli |
| Structural 24h/48h trail | `DENEY` | yok | offline +14/+17R göründü ama 7 günlük outlier ve path/overlap bağımlı; finalist değil |
| breakout 0.0 / 0.6 ATR invalidation | `DENEY` | mevcut breakout 0.35 ATR | offline diagnostic |
| limit entry / post-only | `MEVCUT` | `RangeV2Passive` | H1'de test edildi, NO_GO |
| pyramid/scale-out | `ERTELENEN` | yok | risk geometrisi büyük değişiklik |

**Kural:** offline 1m taraması koşullu diagnostic'tir; girişler, overlap,
kill-switch ve equity yeniden simüle edilmez. Finalistler tam Nautilus
replay'e girer. Stop-first, gap, aynı bar belirsizliği ve trailing'in yalnız
bar kapanışında güncellenmesi korunur.

## 4. No-trade / churn

Kaynak: `v8-core/src/kaizen/chop_suppression.rs:20-45` (A0-A5),
`:60-170` (A1 2.5×friction, A2 8-bar cooldown, A3 compression 0.65, A5).
tradebot4: `scoring.py:50` width/cost ratio, `range_v1.py` regime.

| Aday | Durum | Değerlendirme |
|---|---|---|
| A1 cost/excursion | `VERİ_ENGELLİ` (tam causal değilse) | Beklenen gross excursion karar anında geçmişten fold-içi sızıntısız hesaplanmadan kullanılamaz; gelecekteki MFE girişe konmaz |
| A2 episode cooldown | `DENEY` | Aynı yönde son başarısız kampanyadan 8 bar; tradebot4'te sabit 15m cooldown var, failure-based yok |
| A3 compression quality | `DENEY` | ATR short/long veya BBW percentile ile causal; 0.65 kanıt değil aday |
| A5 composite | `DENEY` | A2+A3; A1 olmadan "composite" denemez |

Filtreler yalnız tamamlanmış trade listesinden eleme olarak test edilirse
selection bias ve değişen sermaye gizlenir; diagnostic ekran ile full replay
ayrılır.

## 5. Sermaye / risk

Kaynak: `v8-core/src/usdm_sim/capital_viability.rs:35-80`:
`E_step = step_size*stop_distance/risk_fraction`,
`E_notional = min_notional*stop_distance/(entry_price*risk_fraction)`,
`E_margin = min_notional/leverage`, `E_crit = max(...)`.
`scenario_ruin.rs:50-130`: bootstrap ruin, time-to-ruin, SaR.

| Fikir | Durum | Not |
|---|---|---|
| E_crit zarfı | `UYARLA` (tanı) | 20/500 ayrı senaryo; lot rounding, iki bacak min, fee/slip reserve, maintenance ayrı test |
| tier tradable share | `DENEY` | 20/500 ayrı; ölçekleme yok |
| bootstrap ruin | `DENEY` | trade net R dizisiyle; 20/500 ayrı |
| SaR / liquidation cascade | `VERİ_ENGELLİ` | L2/liquidation verisi yok; model-derived |
| maker markout | `VERİ_ENGELLİ` | L2 yok; v8'deki 2.5/4.2/1.8 bps hardcoded, kanıt değil |

## 6. Expert adayları

Kaynak: `v8-core/src/experts/*.rs`, `docs/EXPERTS_REGISTRY.yaml`.
Her expert ayrı hipotez/strateji ve ayrı deneme sayısıdır; topluca karar yoluna
eklenmez.

| Expert | Mekanizma | Veri/PIT | tradebot4 uyumu | Durum |
|---|---|---|---|---|
| `failed_breakout` | prior high üstü kapanış, sonra altına dönüş → short | location, ATR, history | zone/breakout tail | `DENEY` |
| `failed_breakout_2b` | significant swing low reclaim after failed breakdown | swing_low_10, candle_shape, ATR | zone + wick | `DENEY` (birinci aday) |
| `liquidity_sweep_reclaim` | prior extreme iğne + close reclaim | prior high/low, ATR | sweep buffer | `DENEY` |
| `breakout_retest` | breached level retest | location, ATR | zone breakout | `DENEY` |
| `trend_pullback`, `trend_pullback_depth` | trend pullback continuation | EMA/trend, location | range-only rejim | `ERTELENEN` |
| `ichimoku_cloud` | Tenkan/Kijun + cloud | volatility, history | yok | `DENEY` (küçük örnek, v8'de 17-18 işlem) |
| `trend_continuation` | trend continuation | trend, volatility | yok | `DENEY` (v8'de 42-49 işlem, iki koşuda değişken) |
| `range_breakout_1to1` | consolidation breakout | location, participation | range ailesi | `DENEY` (v8'de negatif) |
| `bollinger_breakout/reversion` | BB band breakout/fade | volatility, trend | BBW var | `DENEY` |
| `donchian_breakout` | N-bar channel breakout | location | yok | `DENEY` |
| `rsi_stoch_reversion`, `macd_stoch_trend`, `divergence_12_setups` | oscillator | oscillator, location | ADX var | `DENEY` |
| `candlestick_reversal`, `gap_exhaustion` | bar/gap pattern | candle_shape, location | wick/gap | `DENEY` |
| `volume_confirmed_breakout`, `volume_climax_reversal`, `obv_adl_regime` | participation | volume/OBV/ADL | volume median | `DENEY` |
| `floor_trader_pivot`, `market_profile_value_area` | session/value | session, history | yok | `ERTELENEN` |
| `funding_crowding_reversal` | funding crowding | positioning | funding var | `DENEY` |
| `open_interest_divergence` | OI divergence | OI | veri yok | `VERİ_ENGELLİ` |
| `capitulation` | derivatives tape | derivatives | veri yok | `VERİ_ENGELLİ` |
| `pattern_measuring_objective`, `pandf_breakout`, `fib_projection_reversal` | geometric | location/volatility | yok | `ERTELENEN` |

**İlk aday:** `failed_breakout_2b` veya `liquidity_sweep_reclaim`; ikisi de
mevcut range ailesinin breakout tail'ine doğrudan cevap verir. Aynı anda iki
expert uygulanmaz.

## 7. Analiz / tanı

| V8 modülü | Fikir | Durum | Not |
|---|---|---|---|
| `analysis/outcome.rs` | her candidate için counterfactual outcome | `DENEY` | tradebot4'te `missed_opportunities` sınırlı; genişletilebilir |
| `analysis/veto_attribution.rs` | reddedilen adayların reason-coded funnel'ı | `DENEY` | selection bias/availability sınırıyla |
| `analysis/phase1-3` | cube/regret | `ERTELENEN` | yüksek efor; önce basit what-if |
| `oracle/recoverability.rs` | hindsight→PIT→promotable→live waterfall | `PORT_EDİLMEZ` (sabitler) | 45%/55% hardcoded; kanıt değil, kavram olarak not |
| `marketstate` PIT/quality | 4 clock, availability, null, hash | `UYARLA` (kademeli) | tradebot4 bar verisi daha basit; önce feature version/quality |
| `world/*` | cross-asset, structural break, bootstrap | `ERTELENEN` | mevcut Monte Carlo'nun üstüne |
| `assurance/judiciary/benchmark` | governance/evidence fabric | `PORT_EDİLMEZ` | gereksiz governance katmanı |

## 8. Veri engelleri ve bilinmeyenler

- OI, long/short skew, derivatives tape yok: `VERİ_ENGELLİ`.
- L2 depth/queue/liquidation verisi yok: maker fill/queue ve SaR `VERİ_ENGELLİ`.
- Tarihsel exchange filtreleri (min notional/step) run metadata'dan gelir;
  geçmiş geçerliliği sembol/dönem bazında `UNKNOWN` olabilir. Uydurma yok.
- Holdout 2026-07..2026-10 bu görevde açılmaz. Eski 2026-01..06 OOS artık
  development'tır; "fresh OOS" denmez.
- v8-core 106 BTC/+56 USDT burned diagnostic; edge/OOS değil.
- `state.rs` pivotları iki yanlı; tradebot4'te sağ taraf onay gecikmesi
  `zones.py:34` ile modellenir. V8 feature'larını uygularken bu gecikme
  kanıtlanmadan kullanılmaz.

## 9. Audit ve altyapı bulguları

- `src/bot/backtest/validate.py:174` audit exception'ı yalnız warning yapıyor;
  audit başarısızlığı validation raporunda görünmüyordu.
- `src/bot/audit/why.py:440` civarı `reports/research_round1/summary.json`
  kimliğini kontrol etmeden okuyabiliyor; farklı run/config dönemine ait
  hipotez sonuçları yanlış bağlanabilir.
- `append_history` read-modify-write; atomik değil, run_id dışında kaynak
  kimliği kontrol etmiyor. Bu görevde düzeltilir ve test edilir.
- Parquet fiziksel append değildir; her yazma dosyayı yeniden oluşturur.
  Tarihçe append-only mantığıyla korunur, sessiz silme yapılmaz.

## 10. Kapsam dışı / port edilmeyecek

- v8-core'un tam Rust engine'i, Nautilus'un yerine geçmez.
- 77 feature'ın tamamı, 28 expert'in tamamı, assurance/judiciary/benchmark.
- Hardcoded model-derived sabitler (maker markout, recoverability oranları).
- OI/long-short, L2, liquidation verisi olmadan ilgili modeller.
- Canlı/testnet, API key, gerçek emir, push/merge.

## 11. Kanıt bağlantıları

- `docs/research/v8_core_port_candidates.md` (önceki katalog).
- `docs/audits/why_no_edge.md` (mevcut muhasebe).
- `reports/research_round1/report.md`, `reports/baseline_costed/baseline.md`.
- `config/gates.yaml`, `config/data_split.yaml`.
- `reports/research/v8_transfer/exit_scan.md` (bu görev).
- `reports/research/v8_transfer/exit_replay.md` (bu görev).
- `reports/research/v8_transfer/capital_viability.json` (bu görev).
- `reports/research/v8_transfer/feature_ablation.md` (bu görev).
- `reports/research/v8_transfer/chop_scan.md` (bu görev).
- `docs/research/v8_expert_assessment.md` (bu görev).

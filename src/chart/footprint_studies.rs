//! Footprint studies añadidos por el fork, aislados del render principal de
//! `kline.rs`. Hoy: **stacked imbalance** (imbalances diagonales consecutivos
//! del mismo lado, estilo ATAS).
//!
//! Diseño: todo el cómputo y el dibujo viven acá. `kline.rs` solo extrae los
//! parámetros del `FootprintStudy::StackedImbalance` y llama a
//! [`draw_stacked_imbalance`]. Retirar la feature = borrar este archivo + la
//! variante del enum + la llamada (ver `draw_clusters`).

use exchange::unit::{Price, PriceStep};
use iced::theme::palette::Extended;
use iced::widget::canvas::{self, Path, Stroke};
use iced::{Point, Size};

use data::chart::kline::KlineTrades;

/// Un tramo de niveles de precio consecutivos con imbalance diagonal del mismo
/// lado. `is_buy = true` → stack alcista (compradores absorbiendo la oferta).
#[derive(Debug, Clone, Copy)]
pub struct StackedRun {
    pub is_buy: bool,
    pub low: Price,
    pub high: Price,
    pub len: usize,
}

/// Lado del imbalance diagonal de un nivel, con la misma regla que
/// `draw_imbalance_markers` en `kline.rs`:
/// - buy imbalance: `buy@(p+step) > sell@p * (100+thr)/100` (y `>=`).
/// - sell imbalance: `sell@p > buy@(p+step) * (100+thr)/100`.
#[derive(Clone, Copy, PartialEq)]
enum Side {
    Buy,
    Sell,
}

/// Calcula los tramos (`>= min_run`) de imbalance diagonal consecutivo del mismo
/// lado en un footprint de vela. Niveles vacíos entre medias cortan el tramo.
pub fn stacked_imbalance_runs(
    footprint: &KlineTrades,
    step: PriceStep,
    threshold: usize,
    min_run: usize,
    ignore_zeros: bool,
) -> Vec<StackedRun> {
    let min_run = min_run.max(2);

    // Niveles presentes, ordenados ascendente por precio.
    let mut prices: Vec<Price> = footprint.trades.keys().copied().collect();
    if prices.len() < min_run {
        return Vec::new();
    }
    prices.sort_by(|a, b| {
        a.to_f32()
            .partial_cmp(&b.to_f32())
            .unwrap_or(std::cmp::Ordering::Equal)
    });

    let thr_mult = (100 + threshold) as f32 / 100.0;

    // Lado del imbalance por nivel de precio (None = sin imbalance).
    let side_at = |price: Price| -> Option<Side> {
        let group = footprint.trades.get(&price)?;
        let sell_qty = f32::from(group.sell_qty);
        let higher = Price::from_f32(price.to_f32() + step.to_f32_lossy()).round_to_step(step);
        let diagonal_buy_qty = footprint
            .trades
            .get(&higher)
            .map_or(0.0, |g| f32::from(g.buy_qty));

        if ignore_zeros && (sell_qty <= 0.0 || diagonal_buy_qty <= 0.0) {
            return None;
        }

        if diagonal_buy_qty >= sell_qty {
            (diagonal_buy_qty > sell_qty * thr_mult).then_some(Side::Buy)
        } else {
            (sell_qty > diagonal_buy_qty * thr_mult).then_some(Side::Sell)
        }
    };

    let mut runs = Vec::new();
    let mut run_start = 0usize;
    let mut run_side: Option<Side> = None;

    let flush = |runs: &mut Vec<StackedRun>, start: usize, end: usize, side: Option<Side>| {
        if let Some(side) = side {
            let len = end - start;
            if len >= min_run {
                runs.push(StackedRun {
                    is_buy: side == Side::Buy,
                    low: prices[start],
                    high: prices[end - 1],
                    len,
                });
            }
        }
    };

    for i in 0..prices.len() {
        let side = side_at(prices[i]);
        // ¿Sigue el tramo? Mismo lado Y adyacente por tick (sin huecos).
        let adjacent = i > 0 && {
            let expected = Price::from_f32(prices[i - 1].to_f32() + step.to_f32_lossy())
                .round_to_step(step);
            (expected.to_f32() - prices[i].to_f32()).abs() < step.to_f32_lossy() * 0.5
        };

        if side.is_some() && side == run_side && adjacent {
            continue; // extiende el tramo actual
        }

        flush(&mut runs, run_start, i, run_side);
        run_start = i;
        run_side = side;
    }
    flush(&mut runs, run_start, prices.len(), run_side);

    runs
}

/// Dibuja cada tramo como un rectángulo (borde + relleno tenue) que abarca su
/// rango de precios, centrado en la vela. Se dibuja como overlay, por encima de
/// cualquier `ClusterKind`.
#[allow(clippy::too_many_arguments)]
pub fn draw_stacked_imbalance(
    frame: &mut canvas::Frame,
    price_to_y: impl Fn(Price) -> f32,
    x_position: f32,
    cell_width: f32,
    cell_height: f32,
    runs: &[StackedRun],
    palette: &Extended,
) {
    if runs.is_empty() {
        return;
    }

    let left = x_position - (cell_width / 2.0);
    let width = cell_width;

    for run in runs {
        let color = if run.is_buy {
            palette.success.strong.color
        } else {
            palette.danger.strong.color
        };

        let y_top = price_to_y(run.high) - (cell_height / 2.0);
        let y_bot = price_to_y(run.low) + (cell_height / 2.0);
        let height = (y_bot - y_top).max(1.0);

        let rect = Path::rectangle(Point::new(left, y_top), Size::new(width, height));

        frame.fill(&rect, color.scale_alpha(0.10));
        frame.stroke(
            &rect,
            Stroke::default().with_width(1.5).with_color(color),
        );
    }
}

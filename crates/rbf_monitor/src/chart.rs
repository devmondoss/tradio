use iced::mouse;
use iced::widget::canvas::{self, Frame, Geometry, LineDash, Path, Stroke, Text};
use iced::{Color, Point, Rectangle, Renderer, Size, Theme};
use crate::data::{Bar, theme as T};

// Mismos colores que draw_candle_dp en src/chart/kline.rs
const BULL: Color = Color { r: 0.149, g: 0.651, b: 0.604, a: 1.0 }; // #26a69a
const BEAR: Color = Color { r: 0.937, g: 0.325, b: 0.314, a: 1.0 }; // #ef5350

pub struct CandleChart<'a> {
    bars:   &'a [Bar],
    width:  f32,
    height: f32,
}

impl<'a> CandleChart<'a> {
    pub fn draw(&self, frame: &mut Frame) {
        if self.bars.is_empty() { return; }

        let w = self.width;
        let h = self.height;
        let pad_l: f32 = 56.0;
        let pad_r: f32 = 12.0;
        let pad_t: f32 = 10.0;
        let pad_b: f32 = 24.0;

        let chart_w = w - pad_l - pad_r;
        let chart_h = h - pad_t - pad_b;

        let visible_n = 200.min(self.bars.len());
        let visible = &self.bars[self.bars.len() - visible_n..];

        let price_min = visible.iter().map(|b| b.low) .fold(f64::MAX, f64::min) as f32;
        let price_max = visible.iter().map(|b| b.high).fold(f64::MIN, f64::max) as f32;
        let price_range = (price_max - price_min).max(1.0);

        let to_y = |price: f32| pad_t + chart_h * (1.0 - (price - price_min) / price_range);

        // ── Fondo ─────────────────────────────────────────────────────────────
        frame.fill_rectangle(Point::ORIGIN, Size::new(w, h), T::BG);

        // ── Grid horizontal ───────────────────────────────────────────────────
        for i in 0..=4 {
            let price = price_min + price_range * (i as f32 / 4.0);
            let y = to_y(price);

            frame.stroke(
                &Path::line(Point::new(pad_l, y), Point::new(w - pad_r, y)),
                Stroke::default().with_color(T::CARD2).with_width(0.5),
            );
            frame.fill_text(Text {
                content:  format!("{:.1}", price),
                position: Point::new(2.0, y - 6.0),
                color:    T::TEXT3,
                size:     iced::Pixels(9.0),
                font:     iced::Font::with_name("Azeret Mono"),
                ..Text::default()
            });
        }

        // ── Velas — idéntico a draw_candle_dp del chart principal ─────────────
        let cell_w   = chart_w / visible_n as f32;
        let candle_w = cell_w.min(14.0).max(1.5);

        for (i, bar) in visible.iter().enumerate() {
            let x = pad_l + (i as f32 + 0.5) * cell_w;

            let is_bull = bar.close >= bar.open;
            let base_color = if is_bull { BULL } else { BEAR };
            // Vela viva ligeramente más translúcida
            let color = if bar.live { Color { a: 0.65, ..base_color } } else { base_color };

            let y_open  = to_y(bar.open  as f32);
            let y_high  = to_y(bar.high  as f32);
            let y_low   = to_y(bar.low   as f32);
            let y_close = to_y(bar.close as f32);

            // Mecha: fill_rectangle de candle_w/4 de ancho (igual que el original)
            frame.fill_rectangle(
                Point::new(x - candle_w / 8.0, y_high),
                Size::new(candle_w / 4.0, (y_low - y_high).abs()),
                color,
            );
            // Cuerpo
            frame.fill_rectangle(
                Point::new(x - candle_w / 2.0, y_open.min(y_close)),
                Size::new(candle_w, (y_open - y_close).abs().max(1.0)),
                color,
            );
        }

        // ── Línea de precio actual (dashed) ───────────────────────────────────
        if let Some(last) = self.bars.last() {
            let y = to_y(last.close as f32);
            let is_bull = last.close >= last.open;
            let color = if is_bull { BULL } else { BEAR };

            frame.stroke(
                &Path::line(Point::new(pad_l, y), Point::new(w - pad_r, y)),
                Stroke {
                    line_dash: LineDash { segments: &[3.0, 4.0], offset: 0 },
                    ..Stroke::default().with_color(color).with_width(0.8)
                },
            );

            // Tag con el precio
            let label = format!("{:.1}", last.close);
            let tag_w = label.len() as f32 * 6.2 + 8.0;
            frame.fill_rectangle(
                Point::new(w - pad_r - tag_w, y - 8.0),
                Size::new(tag_w, 16.0),
                color,
            );
            frame.fill_text(Text {
                content:  label,
                position: Point::new(w - pad_r - tag_w + 4.0, y - 5.0),
                color:    T::BG,
                size:     iced::Pixels(9.5),
                font:     iced::Font::with_name("Azeret Mono"),
                ..Text::default()
            });
        }
    }
}

// ── Widget ───────────────────────────────────────────────────────────────────

pub struct ChartWidget<'a> {
    pub bars:  &'a [Bar],
    pub cache: &'a canvas::Cache,
}

impl<Message> canvas::Program<Message> for ChartWidget<'_> {
    type State = ();

    fn draw(
        &self,
        _state: &Self::State,
        renderer: &Renderer,
        _theme: &Theme,
        bounds: Rectangle,
        _cursor: mouse::Cursor,
    ) -> Vec<Geometry> {
        let geom = self.cache.draw(renderer, bounds.size(), |frame| {
            CandleChart { bars: self.bars, width: bounds.width, height: bounds.height }
                .draw(frame);
        });
        vec![geom]
    }
}

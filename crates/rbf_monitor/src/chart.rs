use iced::mouse;
use iced::widget::canvas::{self, Frame, Geometry, Path, Stroke, Text};
use iced::{Color, Point, Rectangle, Renderer, Size, Theme};
use crate::data::{Bar, theme as T};

pub struct CandleChart {
    pub bars:    Vec<Bar>,
    pub width:   f32,
    pub height:  f32,
}

impl CandleChart {
    /// Dibuja las velas en el frame
    pub fn draw(&self, frame: &mut Frame) {
        if self.bars.is_empty() { return; }

        let w = self.width;
        let h = self.height;
        let pad_l: f32 = 52.0;
        let pad_r: f32 = 12.0;
        let pad_t: f32 = 10.0;
        let pad_b: f32 = 24.0;

        let chart_w = w - pad_l - pad_r;
        let chart_h = h - pad_t - pad_b;

        // Rango de precio visible (últimas N barras)
        let visible_n = 200.min(self.bars.len());
        let visible = &self.bars[self.bars.len() - visible_n..];

        let price_min = visible.iter().map(|b| b.low)  .fold(f64::MAX, f64::min) as f32;
        let price_max = visible.iter().map(|b| b.high) .fold(f64::MIN, f64::max) as f32;
        let price_range = (price_max - price_min).max(1.0);

        let to_y = |price: f32| -> f32 {
            pad_t + chart_h * (1.0 - (price - price_min) / price_range)
        };

        // Fondo
        frame.fill_rectangle(
            Point::ORIGIN,
            Size::new(w, h),
            T::BG,
        );

        // Grid horizontal (5 líneas)
        for i in 0..=4 {
            let price = price_min + price_range * (i as f32 / 4.0);
            let y = to_y(price);
            let line = Path::line(
                Point::new(pad_l, y),
                Point::new(w - pad_r, y),
            );
            frame.stroke(&line, Stroke::default().with_color(T::CARD2).with_width(0.5));

            // Label precio
            frame.fill_text(Text {
                content: format!("{:.0}", price),
                position: Point::new(2.0, y - 6.0),
                color: T::TEXT3,
                size: iced::Pixels(9.0),
                font: iced::Font::with_name("Azeret Mono"),
                ..Text::default()
            });
        }

        // Velas
        let candle_w = (chart_w / visible_n as f32).min(12.0).max(1.5);
        let body_w = (candle_w * 0.7).max(1.0);

        for (i, bar) in visible.iter().enumerate() {
            let x = pad_l + (i as f32 + 0.5) * (chart_w / visible_n as f32);
            let is_up = bar.close >= bar.open;
            let color = if bar.live {
                // Vela viva — ligeramente más clara
                if is_up { Color { a: 0.7, ..T::UP } } else { Color { a: 0.7, ..T::DOWN } }
            } else {
                if is_up { T::UP } else { T::DOWN }
            };

            let open_y  = to_y(bar.open  as f32);
            let close_y = to_y(bar.close as f32);
            let high_y  = to_y(bar.high  as f32);
            let low_y   = to_y(bar.low   as f32);

            // Mecha
            let wick = Path::line(
                Point::new(x, high_y),
                Point::new(x, low_y),
            );
            frame.stroke(&wick, Stroke::default().with_color(color).with_width(1.0));

            // Cuerpo
            let body_top    = open_y.min(close_y);
            let body_height = (open_y - close_y).abs().max(1.0);
            let body = Path::rectangle(
                Point::new(x - body_w / 2.0, body_top),
                Size::new(body_w, body_height),
            );
            frame.fill(&body, color);
        }

        // Línea de precio actual
        if let Some(last) = self.bars.last() {
            let y = to_y(last.close as f32);
            let line = Path::line(
                Point::new(pad_l, y),
                Point::new(w - pad_r, y),
            );
            let color = if last.close >= last.open { T::UP } else { T::DOWN };
            let stroke = Stroke {
                line_dash: canvas::LineDash { segments: &[3.0, 4.0], offset: 0 },
                ..Stroke::default().with_color(color).with_width(0.8)
            };
            frame.stroke(&line, stroke);

            // Precio actual tag
            let price_label = format!("{:.1}", last.close);
            let tag_w = price_label.len() as f32 * 6.5 + 8.0;
            let tag = Path::rectangle(
                Point::new(w - pad_r - tag_w, y - 8.0),
                Size::new(tag_w, 16.0),
            );
            frame.fill(&tag, color);
            frame.fill_text(Text {
                content: price_label,
                position: Point::new(w - pad_r - tag_w + 4.0, y - 5.0),
                color: T::BG,
                size: iced::Pixels(9.5),
                font: iced::Font::with_name("Azeret Mono"),
                ..Text::default()
            });
        }
    }
}

/// Widget iced que envuelve el CandleChart
pub struct ChartWidget {
    pub bars: Vec<Bar>,
}

impl<Message> canvas::Program<Message> for ChartWidget {
    type State = ();

    fn draw(
        &self,
        _state: &Self::State,
        renderer: &Renderer,
        _theme: &Theme,
        bounds: Rectangle,
        _cursor: mouse::Cursor,
    ) -> Vec<Geometry> {
        let mut frame = Frame::new(renderer, bounds.size());
        let chart = CandleChart {
            bars:   self.bars.clone(),
            width:  bounds.width,
            height: bounds.height,
        };
        chart.draw(&mut frame);
        vec![frame.into_geometry()]
    }
}

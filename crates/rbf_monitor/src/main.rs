mod api;
mod chart;
mod data;
mod ws;

use data::{Bar, RbfSignal, WsMessage, theme as T};
use ws::WsEvent;

use iced::{
    Alignment, Element, Font, Length, Pixels, Subscription, Task,
    widget::{canvas, column, container, row, rule, scrollable, space, text},
};
use std::borrow::Cow;

const WS_URL: &str = "ws://localhost:9001";

const AZERET_MONO_BYTES: &[u8] =
    include_bytes!("../../../assets/fonts/AzeretMono-Regular.ttf");
const AZERET_MONO: Font = Font::with_name("Azeret Mono");

// ── Mensajes ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
enum Message {
    WsEvent(WsEvent),
    BarsLoaded(Vec<Bar>),
    SignalsLoaded(Vec<RbfSignal>),
}

// ── Estado ───────────────────────────────────────────────────────────────────

struct RbfMonitor {
    bars:         Vec<Bar>,
    signals:      Vec<RbfSignal>,
    ws_connected: bool,
}

impl RbfMonitor {
    fn new() -> (Self, Task<Message>) {
        let state = Self { bars: vec![], signals: vec![], ws_connected: false };
        let load = Task::batch(vec![
            Task::future(async { Message::BarsLoaded(api::fetch_bars().await) }),
            Task::future(async { Message::SignalsLoaded(api::fetch_signals().await) }),
        ]);
        (state, load)
    }

    fn update(&mut self, msg: Message) -> Task<Message> {
        match msg {
            Message::BarsLoaded(bars)       => { self.bars    = bars; }
            Message::SignalsLoaded(signals) => { self.signals = signals; }
            Message::WsEvent(ev) => match ev {
                WsEvent::Connected    => { self.ws_connected = true; }
                WsEvent::Disconnected => { self.ws_connected = false; }
                WsEvent::Message(ws_msg) => self.handle_ws(ws_msg),
            },
        }
        Task::none()
    }

    fn handle_ws(&mut self, msg: WsMessage) {
        match msg {
            WsMessage::Tick { ts_ms, open, high, low, close } => {
                if let Some(last) = self.bars.last_mut() {
                    if last.live {
                        last.high  = last.high.max(high);
                        last.low   = last.low.min(low);
                        last.close = close;
                        return;
                    }
                }
                self.bars.push(Bar { ts_ms, open, high, low, close, live: true });
            }
            WsMessage::Bar { ts_ms, open, high, low, close, .. } => {
                if let Some(last) = self.bars.last_mut() {
                    if last.live { last.live = false; }
                }
                self.bars.push(Bar { ts_ms, open, high, low, close, live: false });
                if self.bars.len() > 1500 { self.bars.remove(0); }
            }
            WsMessage::Signal { ts_ms, direction, score, veto, entry, rr, session } => {
                self.signals.insert(0, RbfSignal {
                    id:               ts_ms,
                    timestamp_ms:     ts_ms,
                    direction,
                    session,
                    macro_regime:     None,
                    entry_price:      entry,
                    status:           "OPEN".into(),
                    result_r:         None,
                    exit_reason:      None,
                    confluence_score: score,
                    confluence_flags: None,
                    veto_reason:      veto,
                    rr,
                });
            }
        }
    }

    fn subscription(&self) -> Subscription<Message> {
        ws::connect(WS_URL.into()).map(Message::WsEvent)
    }

    // ── Vista ─────────────────────────────────────────────────────────────

    fn view(&self) -> Element<'_, Message> {
        let navbar = self.view_navbar();
        let body = row![
            self.view_chart(),
            self.view_panels(),
        ]
        .spacing(8)
        .height(Length::Fill);

        container(
            column![navbar, body].spacing(8).height(Length::Fill)
        )
        .padding(10)
        .width(Length::Fill)
        .height(Length::Fill)
        .style(|_: &iced::Theme| container::Style {
            background: Some(T::BG.into()),
            ..Default::default()
        })
        .into()
    }

    fn view_navbar(&self) -> Element<'_, Message> {
        let dot_color = if self.ws_connected { T::UP } else { T::DOWN };
        let status_str = if self.ws_connected { "● live" } else { "● disconnected" };

        let last_price = self.bars.last()
            .map(|b| format!("  {:.1}", b.close))
            .unwrap_or_default();

        container(
            row![
                text("RBF Monitor").color(T::TEXT).size(Pixels(13.0)).font(AZERET_MONO),
                text(" — BTCUSDT.P").color(T::TEXT2).size(Pixels(11.0)).font(AZERET_MONO),
                text(last_price).color(T::ACCENT).size(Pixels(13.0)).font(AZERET_MONO),
                space::horizontal(),
                text(status_str).color(dot_color).size(Pixels(11.0)).font(AZERET_MONO),
            ]
            .align_y(Alignment::Center)
            .spacing(4),
        )
        .padding([6, 10])
        .width(Length::Fill)
        .style(card_style)
        .into()
    }

    fn view_chart(&self) -> Element<'_, Message> {
        container(
            canvas(chart::ChartWidget { bars: self.bars.clone() })
                .width(Length::Fill)
                .height(Length::Fill),
        )
        .style(card_style)
        .width(Length::FillPortion(3))
        .height(Length::Fill)
        .into()
    }

    fn view_panels(&self) -> Element<'_, Message> {
        column![
            self.view_stats(),
            self.view_signals(),
            self.view_vetos(),
        ]
        .spacing(8)
        .width(Length::FillPortion(1))
        .height(Length::Fill)
        .into()
    }

    fn view_stats(&self) -> Element<'_, Message> {
        let closed: Vec<&RbfSignal> = self.signals.iter().filter(|s| s.is_closed()).collect();
        let total   = closed.len();
        let wins    = closed.iter().filter(|s| s.is_win()).count();
        let wr      = if total > 0 { wins as f32 / total as f32 * 100.0 } else { 0.0 };
        let avg_r   = if total > 0 {
            closed.iter().filter_map(|s| s.result_r).sum::<f64>() / total as f64
        } else { 0.0 };
        let open_n  = self.signals.iter().filter(|s| s.is_open()).count();

        let stat_row = |label: String, value: String, vc: iced::Color| -> Element<'_, Message> {
            row![
                text(label).color(T::TEXT2).size(Pixels(10.0)).font(AZERET_MONO),
                space::horizontal(),
                text(value).color(vc).size(Pixels(11.0)).font(AZERET_MONO),
            ]
            .into()
        };

        let wr_color  = if wr  >= 50.0 { T::UP } else { T::DOWN };
        let avg_color = if avg_r >= 0.0 { T::UP } else { T::DOWN };

        container(
            column![
                text("Stats").color(T::TEXT).size(Pixels(11.0)).font(AZERET_MONO),
                rule::horizontal(1.0),
                stat_row("Win rate".into(),  format!("{wr:.1}%"),      wr_color),
                stat_row("Avg R".into(),     format!("{avg_r:.2}R"),   avg_color),
                stat_row("Señales".into(),   format!("{total}"),       T::TEXT),
                stat_row("Abiertas".into(),  format!("{open_n}"),      T::ACCENT),
            ]
            .spacing(6),
        )
        .padding(10)
        .style(card_style)
        .into()
    }

    fn view_signals(&self) -> Element<'_, Message> {
        let rows: Vec<Element<'_, Message>> = self.signals.iter().take(14).map(|s| {
            let dir_color = if s.direction == "Long" { T::UP } else { T::DOWN };
            let r_str = s.result_r
                .map(|r| format!("{:+.2}R", r))
                .unwrap_or_else(|| "open".into());
            let r_color = s.result_r
                .map(|r| if r >= 0.0 { T::UP } else { T::DOWN })
                .unwrap_or(T::TEXT2);
            let score_str = s.confluence_score
                .map(|sc| format!("{}/7", sc))
                .unwrap_or_else(|| "—".into());

            row![
                text(&s.direction[..1]).color(dir_color).size(Pixels(10.0)).font(AZERET_MONO).width(12),
                text(format!("{:.0}", s.entry_price)).color(T::TEXT).size(Pixels(10.0)).font(AZERET_MONO).width(72),
                text(&s.session).color(T::TEXT2).size(Pixels(9.0)).font(AZERET_MONO).width(46),
                text(score_str).color(T::ACCENT).size(Pixels(10.0)).font(AZERET_MONO).width(30),
                space::horizontal(),
                text(r_str).color(r_color).size(Pixels(10.0)).font(AZERET_MONO),
            ]
            .align_y(Alignment::Center)
            .into()
        }).collect();

        container(
            column![
                text("Señales").color(T::TEXT).size(Pixels(11.0)).font(AZERET_MONO),
                rule::horizontal(1.0),
                scrollable(column(rows).spacing(3)).height(Length::Fixed(190.0)),
            ]
            .spacing(6),
        )
        .padding(10)
        .style(card_style)
        .into()
    }

    fn view_vetos(&self) -> Element<'_, Message> {
        let mut map: std::collections::HashMap<String, usize> = Default::default();
        for s in &self.signals {
            if let Some(v) = &s.veto_reason {
                *map.entry(v.clone()).or_default() += 1;
            }
        }
        let mut veto_counts: Vec<(String, usize)> = map.into_iter().collect();
        veto_counts.sort_by_key(|(_, c)| std::cmp::Reverse(*c));

        let top5: Vec<(String, usize)> = veto_counts.into_iter().take(5).collect();
        let veto_rows: Vec<Element<Message>> = top5.into_iter().map(|(name, cnt)| {
            row![
                text(name).color(T::DOWN).size(Pixels(9.0)).font(AZERET_MONO),
                space::horizontal(),
                text(format!("{cnt}")).color(T::TEXT2).size(Pixels(9.0)).font(AZERET_MONO),
            ]
            .into()
        }).collect();

        let body: Element<Message> = if veto_rows.is_empty() {
            text("sin vetos").color(T::TEXT3).size(Pixels(9.0)).font(AZERET_MONO).into()
        } else {
            column(veto_rows).spacing(4).into()
        };

        container(
            column![
                text("Vetos").color(T::TEXT).size(Pixels(11.0)).font(AZERET_MONO),
                rule::horizontal(1.0),
                body,
            ]
            .spacing(6),
        )
        .padding(10)
        .style(card_style)
        .into()
    }
}

fn card_style(_: &iced::Theme) -> container::Style {
    container::Style {
        background: Some(T::CARD.into()),
        border: iced::Border {
            color: T::BORDER,
            width: 1.0,
            radius: 4.0.into(),
        },
        ..Default::default()
    }
}

fn main() -> iced::Result {
    iced::application(RbfMonitor::new, RbfMonitor::update, RbfMonitor::view)
        .title("RBF Monitor")
        .subscription(RbfMonitor::subscription)
        .settings(iced::Settings {
            antialiasing: true,
            fonts: vec![Cow::Borrowed(AZERET_MONO_BYTES)],
            default_text_size: Pixels(11.0),
            ..Default::default()
        })
        .window(iced::window::Settings {
            size: iced::Size::new(1280.0, 800.0),
            min_size: Some(iced::Size::new(900.0, 600.0)),
            ..Default::default()
        })
        .run()
}

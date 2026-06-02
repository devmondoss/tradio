use crate::screen::dashboard::pane::{self, Message};
use crate::style::{self, Icon, icon_text};
use crate::widget::{column_drag, dragger_row};

use data::chart::indicator::{Indicator, UiIndicator};
use iced::{
    Element, Length, padding,
    widget::{button, column, container, pane_grid, row, scrollable, space, text},
};

pub fn view<'a, I>(
    pane: pane_grid::Pane,
    state: &'a pane::State,
    selected: &[I],
    market_type: Option<exchange::adapter::MarketKind>,
) -> Element<'a, Message>
where
    I: Indicator + Copy + Into<UiIndicator>,
{
    let content_allows_dragging = matches!(state.content, pane::Content::Kline { .. });

    let content_row = if let Some(market) = market_type {
        content_row(pane, state, selected, market, content_allows_dragging)
    } else {
        column![].spacing(4).into()
    };

    container(
        scrollable(content_row)
            .height(Length::Shrink)
    )
    .max_width(200)
    .max_height(520)
    .padding(16)
    .style(style::chart_modal)
    .into()
}

fn build_indicator_row<'a, I>(
    pane: pane_grid::Pane,
    indicator: &I,
    is_selected: bool,
) -> Element<'a, Message>
where
    I: Indicator + Copy + Into<UiIndicator>,
{
    let content = if is_selected {
        row![
            text(indicator.to_string()),
            space::horizontal(),
            container(icon_text(Icon::Checkmark, 12)),
        ]
        .width(Length::Fill)
    } else {
        row![text(indicator.to_string())].width(Length::Fill)
    };

    button(content)
        .on_press(Message::PaneEvent(
            pane,
            pane::Event::ToggleIndicator((*indicator).into()),
        ))
        .width(Length::Fill)
        .style(move |theme, status| style::button::modifier(theme, status, is_selected))
        .into()
}

fn selected_list<'a, I>(
    pane: pane_grid::Pane,
    selected: &[I],
    reorderable: bool,
) -> Element<'a, Message>
where
    I: Indicator + Copy + Into<UiIndicator>,
{
    let elements: Vec<Element<_>> = selected
        .iter()
        .map(|indicator| {
            let base = build_indicator_row(pane, indicator, true);
            dragger_row(base, reorderable)
        })
        .collect();

    if reorderable {
        let mut draggable_column = column_drag::Column::new()
            .on_drag(move |event| Message::PaneEvent(pane, pane::Event::ReorderIndicator(event)))
            .spacing(4);
        for element in elements {
            draggable_column = draggable_column.push(element);
        }
        draggable_column.into()
    } else {
        iced::widget::Column::with_children(elements)
            .spacing(4)
            .into()
    }
}

fn available_list<'a, I>(pane: pane_grid::Pane, available: &[I]) -> Element<'a, Message>
where
    I: Indicator + Copy + Into<UiIndicator>,
{
    let elements: Vec<Element<_>> = available
        .iter()
        .map(|indicator| {
            let base = build_indicator_row(pane, indicator, false);
            dragger_row(base, false)
        })
        .collect();

    iced::widget::Column::with_children(elements)
        .spacing(4)
        .into()
}

fn overlay_section<'a>(
    pane: pane_grid::Pane,
    cfg: data::chart::kline::Config,
) -> Element<'a, Message> {
    let btn = |label: &'static str, active: bool, event: pane::Event| {
        let content = if active {
            row![
                text(label).size(12),
                space::horizontal(),
                container(icon_text(Icon::Checkmark, 11)),
            ]
            .width(Length::Fill)
        } else {
            row![text(label).size(12)].width(Length::Fill)
        };
        button(content)
            .on_press(Message::PaneEvent(pane, event))
            .width(Length::Fill)
            .style(move |theme, status| style::button::modifier(theme, status, active))
    };

    column![
        container(text("Overlays").size(13)).padding(padding::top(8).bottom(4)),
        btn("Order Blocks", cfg.show_order_blocks, pane::Event::ToggleOrderBlocks),
        btn("Fair Value Gaps", cfg.show_fvgs, pane::Event::ToggleFvgs),
        btn("Structure", cfg.show_structure, pane::Event::ToggleStructure),
        btn("Liquidations", cfg.show_liq_events, pane::Event::ToggleLiqEvents),
    ]
    .spacing(4)
    .into()
}

fn content_row<'a, I>(
    pane: pane_grid::Pane,
    state: &'a pane::State,
    selected: &[I],
    market: exchange::adapter::MarketKind,
    allows_drag: bool,
) -> Element<'a, Message>
where
    I: Indicator + Copy + Into<UiIndicator>,
{
    let reorderable = allows_drag && selected.len() >= 2;

    let selected_list = if !selected.is_empty() {
        Some(selected_list(pane, selected, reorderable))
    } else {
        None
    };

    let available: Vec<I> = I::for_market(market)
        .iter()
        .filter(|indicator| !selected.contains(indicator))
        .cloned()
        .collect();
    let available_list = if !available.is_empty() {
        Some(available_list(pane, &available))
    } else {
        None
    };

    // Overlay toggles — only shown for Kline/Candles charts
    let overlay_cfg = if let pane::Content::Kline {
        chart: Some(chart),
        kind: data::chart::KlineChartKind::Candles,
        ..
    } = &state.content
    {
        Some(chart.config)
    } else {
        None
    };

    let mut col = iced::widget::Column::new();
    if let Some(sel) = selected_list {
        col = col.push(sel);
    }
    if let Some(avail) = available_list {
        col = col.push(avail);
    }
    if let Some(cfg) = overlay_cfg {
        col = col.push(overlay_section(pane, cfg));
    }

    column![
        container(text("Indicators").size(14)).padding(padding::bottom(8)),
        col.spacing(4)
    ]
    .spacing(4)
    .into()
}

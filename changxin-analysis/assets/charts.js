/* Page-specific ECharts option builders for the Changxin Technology report. */
(function () {
  const palette = {
    blue: "#2563eb",
    teal: "#0f766e",
    gold: "#b7791f",
    rose: "#b83f58",
    grey: "#9aa1ad",
    line: "#e6e8ec",
    ink: "#2f302d"
  };

  const axisCommon = {
    nameTextStyle: { color: "#6b7280" },
    axisLine: { lineStyle: { color: palette.line } },
    axisLabel: { color: "#6b7280" },
    splitLine: { lineStyle: { color: palette.line } }
  };

  function base() {
    return {
      animation: false,
      textStyle: { fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, Helvetica Neue, Arial, sans-serif" },
      tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
      grid: { left: 62, right: 58, top: 54, bottom: 44, containLabel: true }
    };
  }

  const builders = {
    // Revenue (bar, left axis) + attributable net profit (line, right axis)
    "revenue-profit": function (rows) {
      const option = base();
      option.color = [palette.blue, palette.rose];
      option.legend = { top: 6, textStyle: { color: "#566173" } };
      option.xAxis = Object.assign({ type: "category", data: rows.map((r) => r.period) }, axisCommon);
      option.yAxis = [
        Object.assign({ type: "value", name: "营收（亿元）" }, axisCommon),
        Object.assign({ type: "value", name: "归母净利（亿元）" }, axisCommon)
      ];
      option.series = [
        {
          name: "营业总收入",
          type: "bar",
          barMaxWidth: 46,
          itemStyle: { borderRadius: [6, 6, 0, 0] },
          data: rows.map((r) => r.revenue),
          label: { show: true, position: "top", color: "#4b5563", fontSize: 11, formatter: "{c}" }
        },
        {
          name: "归母净利润",
          type: "line",
          yAxisIndex: 1,
          symbolSize: 7,
          lineStyle: { width: 3 },
          data: rows.map((r) => r.net_profit)
        }
      ];
      return option;
    },

    // Quarterly attributable net profit
    quarterly: function (rows) {
      const option = base();
      option.color = [palette.teal];
      option.grid = { left: 62, right: 34, top: 40, bottom: 44, containLabel: true };
      option.xAxis = Object.assign({ type: "category", data: rows.map((r) => r.quarter) }, axisCommon);
      option.yAxis = [Object.assign({ type: "value", name: "亿元" }, axisCommon)];
      option.series = [
        {
          name: "单季归母净利",
          type: "bar",
          barMaxWidth: 58,
          itemStyle: { borderRadius: [6, 6, 0, 0], color: palette.teal },
          data: rows.map((r) => ({
            value: r.profit,
            label: {
              show: true,
              position: "top",
              color: "#4b5563",
              fontSize: 11,
              formatter: r.note ? r.note : "{c}"
            }
          }))
        }
      ];
      return option;
    },

    // DRAM contract price QoQ range, mid-point bar
    "price-qoq": function (rows) {
      const option = base();
      option.color = [palette.gold];
      option.grid = { left: 58, right: 34, top: 40, bottom: 44, containLabel: true };
      option.xAxis = Object.assign({ type: "category", data: rows.map((r) => r.quarter) }, axisCommon);
      option.yAxis = [
        Object.assign({ type: "value", name: "QoQ %", max: 110 }, axisCommon)
      ];
      option.series = [
        {
          name: "合约价环比涨幅（区间中值）",
          type: "bar",
          barMaxWidth: 54,
          itemStyle: { borderRadius: [6, 6, 0, 0], color: palette.gold },
          data: rows.map((r) => ({
            value: r.mid,
            label: {
              show: true,
              position: "top",
              color: "#4b5563",
              fontSize: 11,
              formatter: r.range
            }
          }))
        }
      ];
      return option;
    },

    // Horizontal peer valuation bar
    "peer-pe": function (rows) {
      const reversed = rows.slice().sort((a, b) => a.pe - b.pe);
      const option = {
        animation: false,
        textStyle: { fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, Helvetica Neue, Arial, sans-serif" },
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow" },
          valueFormatter: (value) => value + "x"
        },
        grid: { left: 96, right: 56, top: 24, bottom: 46, containLabel: true },
        xAxis: Object.assign(
          { type: "value", name: "动态PE（倍）", nameLocation: "middle", nameGap: 28, max: 62, interval: 20 },
          axisCommon,
          { axisLabel: { color: "#6b7280", formatter: (value) => (value % 20 === 0 ? value : "") } }
        ),
        yAxis: Object.assign({ type: "category", data: reversed.map((r) => r.name) }, axisCommon),
        series: [
          {
            name: "动态PE",
            type: "bar",
            barMaxWidth: 22,
            data: reversed.map((r) => ({
              value: r.pe,
              itemStyle: {
                color: r.name.indexOf("长鑫") === 0 ? palette.rose : palette.blue,
                borderRadius: [0, 4, 4, 0]
              }
            })),
            label: { show: true, position: "right", color: "#4b5563", fontSize: 11, formatter: "{c}x" }
          }
        ]
      };
      return option;
    },

    // Close price since IPO
    price: function (rows) {
      const option = base();
      option.color = [palette.blue];
      option.grid = { left: 56, right: 30, top: 30, bottom: 52, containLabel: true };
      option.xAxis = Object.assign(
        {
          type: "category",
          data: rows.map((r) => r.date),
          axisLabel: { color: "#6b7280", interval: Math.floor(rows.length / 6) }
        },
        { axisLine: axisCommon.axisLine, splitLine: { show: false } }
      );
      option.yAxis = [Object.assign({ type: "value", name: "元", scale: true }, axisCommon)];
      option.series = [
        {
          name: "收盘价",
          type: "line",
          showSymbol: false,
          smooth: false,
          lineStyle: { width: 2.5 },
          areaStyle: { opacity: 0.08 },
          data: rows.map((r) => r.close),
          markLine: {
            symbol: "none",
            data: [
              {
                yAxis: 61.8,
                label: {
                  formatter: "上市高点 61.80",
                  position: "insideStartTop",
                  align: "left",
                  color: palette.rose,
                  fontSize: 10
                },
                lineStyle: { color: palette.rose, type: "dashed" }
              }
            ]
          }
        }
      ];
      return option;
    }
  };

  window.PageCharts = builders;
})();

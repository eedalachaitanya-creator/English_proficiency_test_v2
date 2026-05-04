import {
  Component,
  ElementRef,
  ViewChild,
  AfterViewInit,
  OnDestroy,
  input,
  effect,
} from '@angular/core';
import { CommonModule } from '@angular/common';

import {
  Chart,
  RadarController,
  RadialLinearScale,
  PointElement,
  LineElement,
  Filler,
  Tooltip,
  Legend,
  ChartConfiguration,
} from 'chart.js';

// Register only the Chart.js pieces this component uses — keeps the
// bundle slim and avoids pulling every controller into the build.
Chart.register(RadarController, RadialLinearScale, PointElement, LineElement, Filler, Tooltip, Legend);

/**
 * Reusable radar (spider) chart for showing one candidate's per-dimension
 * scores at a glance. Used in the HR candidate-detail page for both the
 * writing rubric (5 dims, 0-20) and the speaking rubric (5 dims, 0-100).
 *
 * Usage:
 *   <app-radar-breakdown
 *     [breakdown]="detail()!.writing_breakdown"
 *     [maxValue]="20">
 *   </app-radar-breakdown>
 *
 * Behavior:
 *   - All values are normalized to 0-100 for the chart axis so writing
 *     and speaking radars look comparable in size on the same page.
 *   - Null breakdown → renders an empty placeholder with a note.
 *   - Breakdown present but all values null → flat polygon at 0 + a
 *     "skipped grading" note.
 */
@Component({
  selector: 'app-radar-breakdown',
  standalone: true,
  imports: [CommonModule],
  template: `
    @if (breakdown()) {
      <div class="radar-wrap">
        <canvas #chartCanvas></canvas>
      </div>
      @if (allNull()) {
        <div class="radar-note">
          Grading was skipped — see the feedback paragraph below for details.
        </div>
      }
    } @else {
      <div class="radar-empty">No breakdown available.</div>
    }
  `,
  styles: [`
    .radar-wrap {
      position: relative;
      width: 100%;
      max-width: 320px;
      height: 320px;
      margin: 12px auto 0;
    }
    .radar-note {
      margin-top: 8px;
      font-size: 12px;
      color: var(--text-muted, #666);
      text-align: center;
      font-style: italic;
    }
    .radar-empty {
      padding: 20px;
      text-align: center;
      font-size: 13px;
      color: var(--text-muted, #666);
      font-style: italic;
    }
  `],
})
export class RadarBreakdown implements AfterViewInit, OnDestroy {
  /** Map of dimension key → score, or null if no data exists at all. */
  breakdown = input<Record<string, number | null> | null>(null);
  /** Max value for the dimension (e.g., 20 for writing, 100 for speaking). */
  maxValue = input<number>(100);

  @ViewChild('chartCanvas') canvasRef?: ElementRef<HTMLCanvasElement>;

  private chart: Chart | null = null;
  private viewReady = false;

  constructor() {
    // Re-render whenever the breakdown or maxValue input changes. The
    // viewReady guard ensures we don't try to render before ngAfterViewInit.
    effect(() => {
      const data = this.breakdown();
      const max = this.maxValue();
      if (this.viewReady) this.render(data, max);
    });
  }

  ngAfterViewInit(): void {
    this.viewReady = true;
    this.render(this.breakdown(), this.maxValue());
  }

  ngOnDestroy(): void {
    this.chart?.destroy();
    this.chart = null;
  }

  /** True when breakdown exists but every value is null (skipped-grading case). */
  allNull(): boolean {
    const b = this.breakdown();
    if (!b) return false;
    return Object.values(b).every((v) => v === null);
  }

  private render(
    breakdown: Record<string, number | null> | null,
    max: number,
  ): void {
    if (!breakdown || !this.canvasRef) {
      this.chart?.destroy();
      this.chart = null;
      return;
    }

    const labels = Object.keys(breakdown).map((k) => this.humanize(k));
    // Normalize each value to 0-100 so writing (0-20 native) and speaking
    // (0-100 native) charts have the same visual scale.
    const data = Object.values(breakdown).map((v) =>
      v === null ? 0 : Math.max(0, Math.min(100, (v / max) * 100)),
    );

    const config: ChartConfiguration<'radar'> = {
      type: 'radar',
      data: {
        labels,
        datasets: [
          {
            label: 'Score',
            data,
            backgroundColor: 'rgba(255, 130, 50, 0.18)',
            borderColor: 'rgba(255, 130, 50, 0.95)',
            borderWidth: 2,
            pointBackgroundColor: 'rgba(255, 130, 50, 1)',
            pointBorderColor: '#fff',
            pointHoverRadius: 6,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          r: {
            min: 0,
            max: 100,
            ticks: {
              stepSize: 20,
              showLabelBackdrop: false,
              color: '#888',
              font: { size: 10 },
            },
            pointLabels: { font: { size: 11 } },
            grid: { color: 'rgba(0,0,0,0.08)' },
            angleLines: { color: 'rgba(0,0,0,0.12)' },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              // Show the original (non-normalized) score in the tooltip
              // so HR sees "17 / 20" not "85 / 100" for writing dims.
              label: (ctx) => {
                const idx = ctx.dataIndex;
                const raw = Object.values(breakdown)[idx];
                if (raw === null) return `${ctx.label}: not graded`;
                return `${ctx.label}: ${raw} / ${max}`;
              },
            },
          },
        },
      },
    };

    if (this.chart) {
      // Update in place to preserve animation state and avoid flicker.
      this.chart.data = config.data;
      this.chart.options = config.options!;
      this.chart.update();
    } else {
      this.chart = new Chart(this.canvasRef.nativeElement, config);
    }
  }

  private humanize(key: string): string {
    return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  }
}

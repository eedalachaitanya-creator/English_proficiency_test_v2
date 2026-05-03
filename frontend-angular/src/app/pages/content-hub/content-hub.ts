import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';

import { Topnav } from '../../shared/components/topnav/topnav';
import { Footer } from '../../shared/components/footer/footer';

/**
 * Landing page for HR content authoring (/dashboard/content).
 *
 * Shows 4 cards — passages, questions, writing topics, speaking topics —
 * each linking to a dedicated CRUD list page. The actual list pages will
 * be added in subsequent phases.
 */
@Component({
  selector: 'app-content-hub',
  standalone: true,
  imports: [CommonModule, RouterLink, Topnav, Footer],
  templateUrl: './content-hub.html',
  styleUrl: './content-hub.css',
})
export class ContentHub {
  // No state yet — just a navigation page.
}
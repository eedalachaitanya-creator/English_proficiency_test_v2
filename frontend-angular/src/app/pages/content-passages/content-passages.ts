import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink, Router } from '@angular/router';

import { ApiError } from '../../core/services/api.service';
import { ModalService } from '../../core/services/modal.service';
import {
  HrContentService,
  PassageOut,
  BulkImportResult,
} from '../../core/services/hr-content.service';
import { Topnav } from '../../shared/components/topnav/topnav';
import { Footer } from '../../shared/components/footer/footer';

/**
 * Reading passages management — list, create, edit, delete, bulk-import.
 * Same UX pattern as content-questions but simpler (no options array,
 * no correct_answer, no conditional fields).
 */
@Component({
  selector: 'app-content-passages',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, Topnav, Footer],
  templateUrl: './content-passages.html',
  styleUrl: './content-passages.css',
})
export class ContentPassages implements OnInit {
  private contentSvc = inject(HrContentService);
  private modal = inject(ModalService);
  private router = inject(Router);

  passages = signal<PassageOut[]>([]);
  loading = signal(true);
  loadError = signal('');

  filterDifficulty = signal<'intermediate' | 'expert' | ''>('');

  formOpen = signal(false);
  formMode = signal<'create' | 'edit'>('create');
  editingId = signal<number | null>(null);

  formTitle = signal('');
  formBody = signal('');
  formDifficulty = signal<'intermediate' | 'expert'>('intermediate');
  formTopic = signal('');

  formSubmitting = signal(false);
  formError = signal('');

  csvOpen = signal(false);
  csvFile = signal<File | null>(null);
  csvSubmitting = signal(false);
  csvResult = signal<BulkImportResult | null>(null);
  csvError = signal('');

  // Live word count for body — gives HR feedback that they're hitting the
  // 50-word minimum BEFORE they submit and get a server-side error.
  bodyWordCount = computed(() => {
    const text = this.formBody().trim();
    return text ? text.split(/\s+/).length : 0;
  });

  ngOnInit(): void {
    this.loadPassages();
  }

  private loadPassages(): void {
    this.loading.set(true);
    this.loadError.set('');
    this.contentSvc.listPassages().subscribe({
      next: (p) => {
        this.passages.set(p);
        this.loading.set(false);
      },
      error: (err: ApiError) => {
        if (err.status === 401) {
          this.router.navigate(['/login']);
          return;
        }
        this.loadError.set(err.message || 'Could not load passages.');
        this.loading.set(false);
      },
    });
  }

  filteredPassages = computed(() => {
    const all = this.passages();
    const d = this.filterDifficulty();
    return d ? all.filter(p => p.difficulty === d) : all;
  });

  truncate(text: string, max = 120): string {
    return text.length > max ? text.slice(0, max - 1) + '…' : text;
  }

  openCreateForm(): void {
    this.formMode.set('create');
    this.editingId.set(null);
    this.formTitle.set('');
    this.formBody.set('');
    this.formDifficulty.set('intermediate');
    this.formTopic.set('');
    this.formError.set('');
    this.formOpen.set(true);
  }

  openEditForm(p: PassageOut): void {
    this.formMode.set('edit');
    this.editingId.set(p.id);
    this.formTitle.set(p.title);
    this.formBody.set(p.body);
    this.formDifficulty.set(p.difficulty as 'intermediate' | 'expert');
    this.formTopic.set(p.topic ?? '');
    this.formError.set('');
    this.formOpen.set(true);
  }

  closeForm(): void {
    if (this.formSubmitting()) return;
    this.formOpen.set(false);
  }

  private validateForm(): string | null {
    if (!this.formTitle().trim()) return 'Title is required.';
    if (this.bodyWordCount() < 50) {
      return `Body must be at least 50 words. Current count: ${this.bodyWordCount()}.`;
    }
    return null;
  }

  submitForm(): void {
    const err = this.validateForm();
    if (err) { this.formError.set(err); return; }
    this.formError.set('');
    this.formSubmitting.set(true);

    if (this.formMode() === 'create') {
      this.contentSvc.createPassage({
        title: this.formTitle().trim(),
        body: this.formBody(),
        difficulty: this.formDifficulty(),
        topic: this.formTopic().trim() || null,
      }).subscribe({
        next: (created) => {
          this.passages.update(arr => [created, ...arr]);
          this.formSubmitting.set(false);
          this.formOpen.set(false);
        },
        error: (err: ApiError) => {
          this.formSubmitting.set(false);
          this.formError.set(err.message || 'Could not create passage.');
        },
      });
    } else {
      const id = this.editingId();
      if (id === null) { this.formSubmitting.set(false); return; }
      this.contentSvc.updatePassage(id, {
        title: this.formTitle().trim(),
        body: this.formBody(),
        difficulty: this.formDifficulty(),
        topic: this.formTopic().trim() || null,
      }).subscribe({
        next: (updated) => {
          this.passages.update(arr => arr.map(p => p.id === updated.id ? updated : p));
          this.formSubmitting.set(false);
          this.formOpen.set(false);
        },
        error: (err: ApiError) => {
          this.formSubmitting.set(false);
          this.formError.set(err.message || 'Could not update passage.');
        },
      });
    }
  }

  async onDelete(p: PassageOut): Promise<void> {
    const ok = await this.modal.confirm(
      `Delete this passage?\n\n"${p.title}"\n\nThis cannot be undone.`,
      { okText: 'Delete', cancelText: 'Cancel', dangerous: true, title: 'Confirm delete' }
    );
    if (!ok) return;

    this.contentSvc.deletePassage(p.id).subscribe({
      next: () => {
        this.passages.update(arr => arr.filter(x => x.id !== p.id));
      },
      error: async (err: ApiError) => {
        await this.modal.alert(
          err.message || 'Could not delete passage.',
          { title: err.status === 409 ? 'Cannot delete — passage in use' : 'Delete failed' }
        );
      },
    });
  }

  openCsvModal(): void {
    this.csvFile.set(null);
    this.csvResult.set(null);
    this.csvError.set('');
    this.csvOpen.set(true);
  }

  closeCsvModal(): void {
    if (this.csvSubmitting()) return;
    this.csvOpen.set(false);
  }

  onCsvFileSelected(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0] ?? null;
    this.csvFile.set(file);
    this.csvError.set('');
    this.csvResult.set(null);
  }

  submitCsv(): void {
    const file = this.csvFile();
    if (!file) { this.csvError.set('Choose a CSV file first.'); return; }
    this.csvSubmitting.set(true);
    this.csvError.set('');

    this.contentSvc.bulkImportPassages(file).subscribe({
      next: (result) => {
        this.csvResult.set(result);
        this.csvSubmitting.set(false);
        if (result.created > 0) {
          this.contentSvc.listPassages().subscribe({
            next: (p) => this.passages.set(p),
            error: () => { /* keep old list */ },
          });
        }
      },
      error: (err: ApiError) => {
        this.csvSubmitting.set(false);
        this.csvError.set(err.message || 'Upload failed.');
      },
    });
  }

  trackById = (_: number, p: PassageOut) => p.id;
}
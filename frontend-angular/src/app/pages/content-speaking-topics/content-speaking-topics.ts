import { Component, OnInit, inject, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink, Router } from '@angular/router';

import { ApiError } from '../../core/services/api.service';
import { ModalService } from '../../core/services/modal.service';
import {
  HrContentService,
  SpeakingTopicOut,
} from '../../core/services/hr-content.service';
import { Topnav } from '../../shared/components/topnav/topnav';
import { Footer } from '../../shared/components/footer/footer';

/**
 * Speaking topics management — list, create, edit, delete.
 *
 * Smallest of the 4 entity types:
 *   - Only 3 fields (prompt_text, difficulty, category)
 *   - No CSV import (deliberate — typically only ~8 topics, single form is faster)
 *   - No body, no min/max words, no options array, no passage link
 */
@Component({
  selector: 'app-content-speaking-topics',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, Topnav, Footer],
  templateUrl: './content-speaking-topics.html',
  styleUrl: './content-speaking-topics.css',
})
export class ContentSpeakingTopics implements OnInit {
  private contentSvc = inject(HrContentService);
  private modal = inject(ModalService);
  private router = inject(Router);

  topics = signal<SpeakingTopicOut[]>([]);
  loading = signal(true);
  loadError = signal('');

  filterDifficulty = signal<'intermediate' | 'expert' | ''>('');

  formOpen = signal(false);
  formMode = signal<'create' | 'edit'>('create');
  editingId = signal<number | null>(null);

  formPromptText = signal('');
  formDifficulty = signal<'intermediate' | 'expert'>('intermediate');
  formCategory = signal('');

  formSubmitting = signal(false);
  formError = signal('');

  ngOnInit(): void { this.loadTopics(); }

  private loadTopics(): void {
    this.loading.set(true);
    this.loadError.set('');
    this.contentSvc.listSpeakingTopics().subscribe({
      next: (t) => { this.topics.set(t); this.loading.set(false); },
      error: (err: ApiError) => {
        if (err.status === 401) { this.router.navigate(['/login']); return; }
        this.loadError.set(err.message || 'Could not load speaking topics.');
        this.loading.set(false);
      },
    });
  }

  filteredTopics = computed(() => {
    const all = this.topics();
    const d = this.filterDifficulty();
    return d ? all.filter(t => t.difficulty === d) : all;
  });

  truncate(text: string, max = 120): string {
    return text.length > max ? text.slice(0, max - 1) + '…' : text;
  }

  openCreateForm(): void {
    this.formMode.set('create');
    this.editingId.set(null);
    this.formPromptText.set('');
    this.formDifficulty.set('intermediate');
    this.formCategory.set('');
    this.formError.set('');
    this.formOpen.set(true);
  }

  openEditForm(t: SpeakingTopicOut): void {
    this.formMode.set('edit');
    this.editingId.set(t.id);
    this.formPromptText.set(t.prompt_text);
    this.formDifficulty.set(t.difficulty as 'intermediate' | 'expert');
    this.formCategory.set(t.category ?? '');
    this.formError.set('');
    this.formOpen.set(true);
  }

  closeForm(): void {
    if (this.formSubmitting()) return;
    this.formOpen.set(false);
  }

  private validateForm(): string | null {
    if (!this.formPromptText().trim()) return 'Prompt text is required.';
    return null;
  }

  submitForm(): void {
    const err = this.validateForm();
    if (err) { this.formError.set(err); return; }
    this.formError.set('');
    this.formSubmitting.set(true);

    if (this.formMode() === 'create') {
      this.contentSvc.createSpeakingTopic({
        prompt_text: this.formPromptText().trim(),
        difficulty: this.formDifficulty(),
        category: this.formCategory().trim() || null,
      }).subscribe({
        next: (created) => {
          this.topics.update(arr => [created, ...arr]);
          this.formSubmitting.set(false);
          this.formOpen.set(false);
        },
        error: (err: ApiError) => {
          this.formSubmitting.set(false);
          this.formError.set(err.message || 'Could not create speaking topic.');
        },
      });
    } else {
      const id = this.editingId();
      if (id === null) { this.formSubmitting.set(false); return; }
      this.contentSvc.updateSpeakingTopic(id, {
        prompt_text: this.formPromptText().trim(),
        difficulty: this.formDifficulty(),
        category: this.formCategory().trim() || null,
      }).subscribe({
        next: (updated) => {
          this.topics.update(arr => arr.map(t => t.id === updated.id ? updated : t));
          this.formSubmitting.set(false);
          this.formOpen.set(false);
        },
        error: (err: ApiError) => {
          this.formSubmitting.set(false);
          this.formError.set(err.message || 'Could not update speaking topic.');
        },
      });
    }
  }

  async onDelete(t: SpeakingTopicOut): Promise<void> {
    const ok = await this.modal.confirm(
      `Delete this speaking topic?\n\n"${this.truncate(t.prompt_text, 100)}"\n\nThis cannot be undone.`,
      { okText: 'Delete', cancelText: 'Cancel', dangerous: true, title: 'Confirm delete' }
    );
    if (!ok) return;

    this.contentSvc.deleteSpeakingTopic(t.id).subscribe({
      next: () => {
        this.topics.update(arr => arr.filter(x => x.id !== t.id));
      },
      error: async (err: ApiError) => {
        await this.modal.alert(
          err.message || 'Could not delete speaking topic.',
          { title: err.status === 409 ? 'Cannot delete — topic in use' : 'Delete failed' }
        );
      },
    });
  }

  trackById = (_: number, t: SpeakingTopicOut) => t.id;
}
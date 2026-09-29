/** Starter questions answered from the seeded knowledge base (SOPs, recipes and onboarding guides). */
export const STARTER_PROMPTS: { key: string; label: string; description?: string }[] = [
  {
    key: 'cooling',
    label: 'How do I cool a stockpot of chili safely?',
    description: 'Then try the follow-up “And rice?”',
  },
  { key: 'fryer', label: 'How often should we change the fryer oil, and do we have enough?' },
  { key: 'allergen', label: 'What are the allergen rules for my station?' },
  { key: 'walk-in', label: 'The walk-in alarm is going off — what do I do?' },
  { key: 'knife', label: 'Knife safety basics for a new prep cook?' },
  {
    key: 'croissant',
    label: 'What temperature should the butter block be for laminating croissant dough?',
  },
];

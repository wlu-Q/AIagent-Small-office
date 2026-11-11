export const additionalMaleCharacterTextures = ['ethan', 'noah', 'victor', 'leo'] as const

export const additionalFemaleCharacterTextures = [
  'olivia',
  'chloe',
  'maya',
  'luna',
  'emma',
] as const

export const additionalCharacterTextures = [
  ...additionalMaleCharacterTextures,
  ...additionalFemaleCharacterTextures,
] as const

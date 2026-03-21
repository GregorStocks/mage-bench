package mage.client.bridge;

record ManaPlanEntry(String type, String value, Integer abilityIndex) {
    ManaPlanEntry(String type, String value) {
        this(type, value, null);
    }
}

(function () {
    var local = location.protocol === 'file:' ||
        location.hostname === 'localhost' ||
        location.hostname === '127.0.0.1' ||
        location.hostname === '[::1]';

    window.assetImageUrl = function (path) {
        var id = window.DRIVE_IMAGE_IDS && window.DRIVE_IMAGE_IDS[path];
        if (!local && id) {
            return 'https://lh3.googleusercontent.com/d/' + encodeURIComponent(id);
        }
        return path;
    };
}());

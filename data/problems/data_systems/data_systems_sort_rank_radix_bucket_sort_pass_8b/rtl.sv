module data_systems_sort_rank_radix_bucket_sort_pass_8b (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_last,
    input  logic [7:0]  in_bucket,
    input  logic [31:0] in_payload,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [7:0]  out_bucket,
    output logic [31:0] out_payload,
    output logic        out_last
);

    typedef enum logic [1:0] {COLLECT, PREFIX, SCATTER, EMIT} phase_t;
    phase_t phase;
    logic [7:0] buckets [0:255], sorted_buckets [0:255];
    logic [31:0] payloads [0:255], sorted_payloads [0:255];
    logic [8:0] histogram [0:255], cursor [0:255], prefix [0:255];
    logic [8:0] length, scan, emitted;
    integer total;
    always_comb begin
        total = 0;
        for (integer b = 0; b < 256; b++) begin
            prefix[b] = 9'(total);
            total = total + int'(histogram[b]);
        end
    end
    assign in_ready = phase == COLLECT && length < 256;
    assign out_valid = phase == EMIT;
    assign out_bucket = out_valid ? sorted_buckets[emitted[7:0]] : 8'd0;
    assign out_payload = out_valid ? sorted_payloads[emitted[7:0]] : 32'd0;
    assign out_last = out_valid && emitted + 9'd1 == length;

    always_ff @(posedge clk) begin
        if (rst) begin
            phase <= COLLECT;
            length <= 0;
            scan <= 0;
            emitted <= 0;
            for (integer b = 0; b < 256; b++) begin
                histogram[b] <= 0;
                cursor[b] <= 0;
            end
        end else begin
            case (phase)
                COLLECT: if (in_valid && in_ready) begin
                    buckets[length[7:0]] <= in_bucket;
                    payloads[length[7:0]] <= in_payload;
                    histogram[in_bucket] <= histogram[in_bucket] + 9'd1;
                    length <= length + 9'd1;
                    if (in_last) phase <= PREFIX;
                end
                PREFIX: begin
                    for (integer b = 0; b < 256; b++) cursor[b] <= prefix[b];
                    scan <= 0;
                    phase <= SCATTER;
                end
                SCATTER: begin
                    sorted_buckets[cursor[buckets[scan[7:0]]][7:0]] <= buckets[scan[7:0]];
                    sorted_payloads[cursor[buckets[scan[7:0]]][7:0]] <= payloads[scan[7:0]];
                    cursor[buckets[scan[7:0]]] <= cursor[buckets[scan[7:0]]] + 9'd1;
                    scan <= scan + 9'd1;
                    if (scan + 9'd1 == length) begin
                        phase <= EMIT;
                        emitted <= 0;
                    end
                end
                EMIT: if (out_ready) begin
                    emitted <= emitted + 9'd1;
                    if (out_last) begin
                        phase <= COLLECT;
                        length <= 0;
                        emitted <= 0;
                        for (integer b = 0; b < 256; b++) histogram[b] <= 0;
                    end
                end
            endcase
        end
    end
endmodule

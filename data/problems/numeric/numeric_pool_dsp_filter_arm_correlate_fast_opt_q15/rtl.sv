module correlate_fast_opt_q15_single_output (
    input  logic clk, rst,
    input  logic in_valid,
    output logic in_ready,
    input  logic [7:0] in_len_a, in_len_b, in_a_base, in_b_base,
    input  logic [8:0] in_out_index,
    output logic out_valid,
    input  logic out_ready,
    output logic [15:0] out_sample,
    output logic out_nonzero_region,
    output logic a_req_valid,
    output logic [7:0] a_req_addr,
    input  logic a_req_ready, a_resp_valid,
    input  logic [15:0] a_resp_rdata,
    output logic b_req_valid,
    output logic [7:0] b_req_addr,
    input  logic b_req_ready, b_resp_valid,
    input  logic [15:0] b_resp_rdata
);
    typedef enum logic [2:0] {IDLE, SCAN, READ_A, WAIT_A, READ_B, WAIT_B, DONE} phase_t;
    phase_t phase;
    logic [7:0] length_a, length_b, base_a, base_b, longest;
    logic [8:0] index, element;
    logic signed [10:0] lag, b_element;
    logic signed [15:0] a_sample;
    logic signed [31:0] accumulator;
    logic [8:0] destination_size;

    function automatic logic [15:0] sat16(input logic signed [31:0] value);
        if (value > 32767) sat16=16'h7fff;
        else if (value < -32768) sat16=16'h8000;
        else sat16=value[15:0];
    endfunction

    assign in_ready = phase==IDLE;
    assign out_valid = phase==DONE;
    assign destination_size = ({1'b0, (in_len_a>=in_len_b ? in_len_a : in_len_b)} << 1)-9'd1;
    assign lag = $signed({2'b0,index})-$signed({3'b0,longest})+11'sd1;
    assign b_element = $signed({2'b0,element})-lag;
    assign a_req_valid = phase==READ_A;
    assign b_req_valid = phase==READ_B;
    assign a_req_addr=base_a+element[7:0];
    assign b_req_addr=base_b+b_element[7:0];

    always_ff @(posedge clk) begin
        if (rst) begin
            phase<=IDLE;
            length_a<=0; length_b<=0; base_a<=0; base_b<=0; longest<=0;
            index<=0; element<=0; a_sample<=0; accumulator<=0;
            out_sample<=0; out_nonzero_region<=0;
        end else begin
            case (phase)
                IDLE: if (in_valid) begin
                    length_a<=in_len_a; length_b<=in_len_b;
                    base_a<=in_a_base; base_b<=in_b_base;
                    longest<=in_len_a>=in_len_b ? in_len_a : in_len_b;
                    index<=in_out_index; element<=0; accumulator<=0;
                    out_sample<=0; out_nonzero_region<=0;
                    if (in_len_a==0 || in_len_b==0 || in_out_index>=destination_size)
                        phase<=DONE;
                    else if ((in_len_a>=in_len_b && in_out_index<({1'b0,in_len_a}-{1'b0,in_len_b})) ||
                             (in_len_a<in_len_b && in_out_index>=({1'b0,in_len_a}+{1'b0,in_len_b}-9'd1)))
                        phase<=DONE;
                    else begin
                        out_nonzero_region<=1;
                        phase<=SCAN;
                    end
                end
                SCAN: begin
                    if (element>={1'b0,length_a}) begin
                        out_sample<=sat16(accumulator >>> 15);
                        phase<=DONE;
                    end else if (b_element<0 || b_element>=$signed({3'b0,length_b}))
                        element<=element+9'd1;
                    else phase<=READ_A;
                end
                READ_A: if (a_req_ready) begin
                    if (a_resp_valid) begin a_sample<=a_resp_rdata; phase<=READ_B; end
                    else phase<=WAIT_A;
                end
                WAIT_A: if (a_resp_valid) begin a_sample<=a_resp_rdata; phase<=READ_B; end
                READ_B: if (b_req_ready) begin
                    if (b_resp_valid) begin
                        accumulator<=accumulator+a_sample*$signed(b_resp_rdata);
                        element<=element+9'd1; phase<=SCAN;
                    end else phase<=WAIT_B;
                end
                WAIT_B: if (b_resp_valid) begin
                    accumulator<=accumulator+a_sample*$signed(b_resp_rdata);
                    element<=element+9'd1; phase<=SCAN;
                end
                DONE: if (out_ready) phase<=IDLE;
                default: phase<=IDLE;
            endcase
        end
    end
endmodule

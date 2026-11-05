module matrix_scale_fp32_4x4 (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_src_rows,
    input  logic [15:0]  in_src_cols,
    input  logic [15:0]  in_dst_rows,
    input  logic [15:0]  in_dst_cols,
    input  logic [15:0]  in_src_base_addr,
    input  logic [15:0]  in_dst_base_addr,
    input  logic [31:0]  in_scale,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [1:0]   out_status,

    output logic         data_req_valid,
    output logic         data_req_we,
    output logic [15:0]  data_req_addr,
    output logic [31:0]  data_req_wdata,
    input  logic         data_req_ready,
    input  logic         data_resp_valid,
    input  logic [31:0]  data_resp_rdata
);

function automatic logic [63:0] fp_shift_jam(input logic [63:0] x, input integer n);
    if (n <= 0) fp_shift_jam = x;
    else if (n >= 64) fp_shift_jam = {63'b0, |x};
    else fp_shift_jam = (x >> n) | {63'b0, |(x << (64-n))};
endfunction

function automatic logic [31:0] fp_round_pack(
    input logic sign_bit, input integer exponent, input logic [63:0] sig
);
    integer e;
    logic [63:0] shifted, rounded;
    begin
        e = exponent;
        shifted = sig;
        if (e < 1) begin
            shifted = fp_shift_jam(shifted, 1-e);
            e = 1;
        end
        rounded = (shifted >> 3) + {63'b0, (shifted[2] &&
                  (shifted[1] || shifted[0] || shifted[3]))};
        if (rounded[24]) begin
            rounded = rounded >> 1;
            e = e+1;
        end
        if (e >= 255) fp_round_pack = {sign_bit, 8'hff, 23'b0};
        else if (rounded < 64'h800000)
            fp_round_pack = {sign_bit, 8'b0, rounded[22:0]};
        else fp_round_pack = {sign_bit, e[7:0], rounded[22:0]};
    end
endfunction

function automatic logic [31:0] fp32_mul_bits(input logic [31:0] a, b);
    integer ea, eb, e, i;
    logic sign_bit;
    logic [63:0] ma, mb, product, sig;
    begin
        sign_bit = a[31] ^ b[31];
        ea = a[30:23] == 0 ? 1 : int'(a[30:23]);
        eb = b[30:23] == 0 ? 1 : int'(b[30:23]);
        ma = {40'b0, (a[30:23] != 0), a[22:0]};
        mb = {40'b0, (b[30:23] != 0), b[22:0]};
        if ((a[30:0] > 31'h7f800000) || (b[30:0] > 31'h7f800000) ||
            ((a[30:0] == 31'h7f800000) && (b[30:0] == 0)) ||
            ((b[30:0] == 31'h7f800000) && (a[30:0] == 0)))
            fp32_mul_bits = 32'h7fc00000;
        else if ((a[30:0] == 31'h7f800000) || (b[30:0] == 31'h7f800000))
            fp32_mul_bits = {sign_bit, 8'hff, 23'b0};
        else if ((a[30:0] == 0) || (b[30:0] == 0))
            fp32_mul_bits = {sign_bit, 31'b0};
        else begin
            for (i=0; i<23; i=i+1) begin
                if (!ma[23]) begin ma=ma<<1; ea=ea-1; end
                if (!mb[23]) begin mb=mb<<1; eb=eb-1; end
            end
            product = ma*mb;
            e = ea+eb-127;
            if (product[47]) begin sig=fp_shift_jam(product,21); e=e+1; end
            else sig=fp_shift_jam(product,20);
            fp32_mul_bits = fp_round_pack(sign_bit, e, sig);
        end
    end
endfunction

    typedef enum logic [2:0] {
        ST_IDLE,
        ST_REQ_READ,
        ST_WAIT_READ,
        ST_REQ_WRITE,
        ST_DONE
    } state_t;

    state_t       state;
    logic [15:0]  src_base_reg;
    logic [15:0]  dst_base_reg;
    logic [31:0]  scale_reg;
    logic [3:0]   index_reg;
    logic [31:0]  write_data_reg;
    logic [1:0]   status_reg;
    logic         busy;

    wire output_consumed = out_valid & out_ready;
    wire input_accepted  = in_valid & in_ready;
    wire dims_ok         = (in_src_rows == 16'd4) && (in_src_cols == 16'd4) &&
                           (in_dst_rows == 16'd4) && (in_dst_cols == 16'd4);

    assign in_ready   = (~busy) | output_consumed;
    assign out_valid  = busy & (state == ST_DONE);
    assign out_status = status_reg;

    always_comb begin
        data_req_valid = 1'b0;
        data_req_we    = 1'b0;
        data_req_addr  = 16'd0;
        data_req_wdata = 32'd0;

        case (state)
            ST_REQ_READ: begin
                data_req_valid = 1'b1;
                data_req_we    = 1'b0;
                data_req_addr  = src_base_reg + {12'd0, index_reg};
            end
            ST_REQ_WRITE: begin
                data_req_valid = 1'b1;
                data_req_we    = 1'b1;
                data_req_addr  = dst_base_reg + {12'd0, index_reg};
                data_req_wdata = write_data_reg;
            end
            default: begin
            end
        endcase
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            state          <= ST_IDLE;
            src_base_reg   <= 16'd0;
            dst_base_reg   <= 16'd0;
            scale_reg      <= 32'd0;
            index_reg      <= 4'd0;
            write_data_reg <= 32'd0;
            status_reg     <= 2'b00;
            busy           <= 1'b0;
        end else begin
            if (input_accepted) begin
                busy           <= 1'b1;
                src_base_reg   <= in_src_base_addr;
                dst_base_reg   <= in_dst_base_addr;
                scale_reg      <= in_scale;
                index_reg      <= 4'd0;
                write_data_reg <= 32'd0;
                if (dims_ok) begin
                    status_reg <= 2'b00;
                    state      <= ST_REQ_READ;
                end else begin
                    status_reg <= 2'b01;
                    state      <= ST_DONE;
                end
            end else if (output_consumed) begin
                busy           <= 1'b0;
                state          <= ST_IDLE;
                src_base_reg   <= 16'd0;
                dst_base_reg   <= 16'd0;
                scale_reg      <= 32'd0;
                index_reg      <= 4'd0;
                write_data_reg <= 32'd0;
                status_reg     <= 2'b00;
            end else begin
                case (state)
                    ST_REQ_READ: begin
                        if (data_req_ready) begin
                            state <= ST_WAIT_READ;
                        end
                    end

                    ST_WAIT_READ: begin
                        if (data_resp_valid) begin
                            write_data_reg <= fp32_mul_bits(data_resp_rdata, scale_reg);
                            state          <= ST_REQ_WRITE;
                        end
                    end

                    ST_REQ_WRITE: begin
                        if (data_req_ready) begin
                            if (index_reg == 4'd15) begin
                                state <= ST_DONE;
                            end else begin
                                index_reg <= index_reg + 4'd1;
                                state     <= ST_REQ_READ;
                            end
                        end
                    end

                    default: begin
                        // ST_IDLE and ST_DONE: hold; transitions are
                        // driven by input_accepted / output_consumed
                        // above.
                    end
                endcase
            end
        end
    end

endmodule

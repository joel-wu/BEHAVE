module galois_permutation_core (
    input  logic        clk,
    input  logic        rst,

    input  logic        in_valid,
    output logic        in_ready,
    input  logic        in_mode,
    input  logic [4:0]  in_base_addr,
    input  logic [4:0]  in_galois_elt,
    input  logic [7:0]  in_modulus,

    output logic        out_valid,
    input  logic        out_ready,
    output logic [4:0]  out_base_addr,

    output logic        coeff_mem_req_valid,
    output logic        coeff_mem_req_we,
    output logic [4:0]  coeff_mem_req_addr,
    output logic [7:0]  coeff_mem_req_wdata,
    input  logic        coeff_mem_req_ready,
    input  logic        coeff_mem_resp_valid,
    input  logic [7:0]  coeff_mem_resp_rdata
);

    typedef enum logic [2:0] {
        ST_IDLE,
        ST_READ_REQ,
        ST_READ_RESP,
        ST_COMPUTE,
        ST_WRITE_REQ,
        ST_DONE
    } state_t;

    state_t state;

    logic       mode_reg;
    logic [4:0] base_reg;
    logic [4:0] galois_reg;
    logic [7:0] modulus_reg;

    logic [4:0] read_idx;
    logic [4:0] write_idx;
    logic [4:0] compute_idx;

    logic [7:0] operand [0:15];
    logic [7:0] result_buf [0:15];

    integer j;

    function automatic logic [4:0] reverse_bits_5(input logic [4:0] v);
        begin
            reverse_bits_5 = 5'b0;
            reverse_bits_5[4] = v[0];
            reverse_bits_5[3] = v[1];
            reverse_bits_5[2] = v[2];
            reverse_bits_5[1] = v[3];
            reverse_bits_5[0] = v[4];
        end
    endfunction

    function automatic logic [3:0] reverse_bits_4(input logic [3:0] v);
        begin
            reverse_bits_4 = 4'b0;
            reverse_bits_4[3] = v[0];
            reverse_bits_4[2] = v[1];
            reverse_bits_4[1] = v[2];
            reverse_bits_4[0] = v[3];
        end
    endfunction

    wire input_accepted;
    wire output_consumed;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;

    assign in_ready  = (state == ST_IDLE);
    assign out_valid = (state == ST_DONE);
    assign out_base_addr = base_reg;

    always_comb begin
        coeff_mem_req_valid = 1'b0;
        coeff_mem_req_we    = 1'b0;
        coeff_mem_req_addr  = 5'b0;
        coeff_mem_req_wdata = 8'b0;

        case (state)
            ST_READ_REQ: begin
                coeff_mem_req_valid = 1'b1;
                coeff_mem_req_we    = 1'b0;
                coeff_mem_req_addr  = base_reg + read_idx;
                coeff_mem_req_wdata = 8'b0;
            end
            ST_WRITE_REQ: begin
                coeff_mem_req_valid = 1'b1;
                coeff_mem_req_we    = 1'b1;
                coeff_mem_req_addr  = base_reg + write_idx;
                coeff_mem_req_wdata = result_buf[write_idx[3:0]];
            end
            default: begin
            end
        endcase
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            state      <= ST_IDLE;
            mode_reg   <= 1'b0;
            base_reg   <= 5'b0;
            galois_reg <= 5'b0;
            modulus_reg <= 8'b0;
            read_idx   <= 5'b0;
            write_idx  <= 5'b0;
            compute_idx <= 5'b0;
            for (j = 0; j < 16; j = j + 1) begin
                operand[j]    <= 8'b0;
                result_buf[j] <= 8'b0;
            end
        end else begin
            case (state)
                ST_IDLE: begin
                    if (input_accepted) begin
                        mode_reg    <= in_mode;
                        base_reg    <= in_base_addr;
                        galois_reg  <= in_galois_elt;
                        modulus_reg <= in_modulus;
                        read_idx    <= 5'b0;
                        write_idx   <= 5'b0;
                        compute_idx <= 5'b0;
                        for (j = 0; j < 16; j = j + 1) begin
                            operand[j]    <= 8'b0;
                            result_buf[j] <= 8'b0;
                        end
                        state <= ST_READ_REQ;
                    end
                end

                ST_READ_REQ: begin
                    if (coeff_mem_req_valid && coeff_mem_req_ready) begin
                        state <= ST_READ_RESP;
                    end
                end

                ST_READ_RESP: begin
                    if (coeff_mem_resp_valid) begin
                        operand[read_idx[3:0]] <= coeff_mem_resp_rdata;
                        if (read_idx == 5'd15) begin
                            compute_idx <= 5'b0;
                            state <= ST_COMPUTE;
                        end else begin
                            read_idx <= read_idx + 5'd1;
                            state <= ST_READ_REQ;
                        end
                    end
                end

                ST_COMPUTE: begin
                    if (mode_reg == 1'b0) begin
                        logic [8:0] index_raw_tmp;
                        logic [3:0] index_tmp;
                        logic [7:0] result_value_tmp;

                        index_raw_tmp = compute_idx * galois_reg;
                        index_tmp = index_raw_tmp[3:0];
                        result_value_tmp = operand[compute_idx[3:0]];

                        if (index_raw_tmp[4]) begin
                            if (operand[compute_idx[3:0]] == 8'b0) begin
                                result_value_tmp = 8'b0;
                            end else begin
                                result_value_tmp = modulus_reg - operand[compute_idx[3:0]];
                            end
                        end

                        result_buf[index_tmp] <= result_value_tmp;
                    end else begin
                        logic [4:0] x_tmp;
                        logic [4:0] reversed_tmp;
                        logic [9:0] product_tmp;
                        logic [8:0] index_raw_tmp;
                        logic [3:0] index_tmp;
                        logic [3:0] source_index_tmp;

                        x_tmp = 5'd16 + compute_idx;
                        reversed_tmp = reverse_bits_5(x_tmp);
                        product_tmp = galois_reg * reversed_tmp;
                        index_raw_tmp = product_tmp[9:1];
                        index_tmp = index_raw_tmp[3:0];
                        source_index_tmp = reverse_bits_4(index_tmp);

                        result_buf[compute_idx[3:0]] <= operand[source_index_tmp];
                    end

                    if (compute_idx == 5'd15) begin
                        write_idx <= 5'b0;
                        state <= ST_WRITE_REQ;
                    end else begin
                        compute_idx <= compute_idx + 5'd1;
                    end
                end

                ST_WRITE_REQ: begin
                    if (coeff_mem_req_valid && coeff_mem_req_ready) begin
                        if (write_idx == 5'd15) begin
                            state <= ST_DONE;
                        end else begin
                            write_idx <= write_idx + 5'd1;
                            state <= ST_WRITE_REQ;
                        end
                    end
                end

                ST_DONE: begin
                    if (output_consumed) begin
                        state <= ST_IDLE;
                    end
                end

            endcase
        end
    end

endmodule